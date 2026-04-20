"""
GET  /memory/facts      — user facts store
GET  /memory/search     — semantic search over vector store
GET  /memory/stats      — counts and spend summary
POST /memory/facts      — manually set a fact
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from buddy.memory.store import get_facts, upsert_fact
from buddy.memory.vectors import search_memory, memory_count

router = APIRouter(prefix="/memory", tags=["memory"])


class FactUpsert(BaseModel):
    key: str
    value: str


@router.get("/facts")
async def facts():
    return {"facts": get_facts()}


@router.post("/facts")
async def set_fact(body: FactUpsert):
    upsert_fact(body.key, body.value, source="manual")
    return {"key": body.key, "value": body.value}


@router.get("/search")
async def search(q: str, n: int = 5):
    results = search_memory(q, n_results=n)
    return {"results": results}


@router.get("/stats")
async def stats():
    return {
        "vector_memory_chunks": memory_count(),
        "facts_count": len(get_facts()),
    }


# ── Tools catalogue endpoint ───────────────────────────────────────────────────

@router.get("/tools")
async def list_tools():
    """
    Return the full tool catalogue: name, description, parameter summary,
    and whether the tool requires human approval (human_gate).
    """
    from buddy.tools.tool_registry import TOOLS, _TOOL_MAP
    from buddy.config import settings as cfg

    result = []
    for t in TOOLS:
        fn = t.schema.get("function", {})
        name = fn.get("name", "")
        params = fn.get("parameters", {}).get("properties", {})
        required = fn.get("parameters", {}).get("required", [])
        result.append({
            "name": name,
            "description": fn.get("description", ""),
            "parameters": [
                {
                    "name": k,
                    "type": v.get("type", ""),
                    "description": v.get("description", ""),
                    "required": k in required,
                }
                for k, v in params.items()
            ],
            "human_gate": t.human_gate,
            "disabled": name in cfg.disabled_tools,
        })
    return {"tools": result, "count": len(result)}
