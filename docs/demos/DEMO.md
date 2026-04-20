# Buddy — Live Demo Script
_5 beats, ~10 minutes, no slides needed_

---

## Prerequisites
- buddy server running: `cd ~/Projects/buddy && .venv/bin/python -m buddy.main`
- Browser open at: http://localhost:7437
- Terminal visible alongside browser

---

## Beat 1 — Local Routing (~60s)
**What to do:** Type into the chat box:
```
What is the speed of light? One sentence.
```

**What happens:**
- Response appears in ~30s (qwen2.5:14b, warm) or ~2min (cold start)
- Model badge shows `qwen2.5:14b`
- Grade panel shows score (expect 80–90/100)

**What to say:**
> "This ran entirely on-device. No API call. The response is graded automatically by a second local model — phi4-mini — using the cus-core rubric engine. You can see the score breakdown right in the UI."

---

## Beat 2 — Keyword Escalation (~15s)
**What to do:** Type:
```
Write code for a Python quicksort function with inline comments.
```

**What happens:**
- Response in ~10s (Opus 4.7 — keyword "write code" triggers immediate escalation)
- Model badge shows `opus 4.7` with orange frontier style
- Grade shows 95–100/100 with `↑ escalated` badge

**What to say:**
> "The router detected 'write code' and escalated directly to Opus 4.7 without wasting time on the local model. Smart routing — API credits only spent when they're worth spending."

---

## Beat 3 — Extended Thinking (shown during Beat 2 result)
**What to do:** Click the `▸` toggle on the grade panel to expand it.

**What happens:**
- Rubric bars expand (relevance, accuracy, conciseness, safety)
- 🧠 thinking trace appears — Haiku's full reasoning before it scored

**What to say:**
> "This is the cus-core moat. The grader doesn't just give a number — it shows you why. Haiku used extended thinking to reason through each criterion before scoring. Trust is verifiable, not just claimed."

---

## Beat 4 — Expected-Failure Demo (~15s)
**What to do:** Click the **Demo** tab → click **Phishing** scenario card → click Run

**What happens:**
- "Running demo…" loading state
- Opus 4.7 refuses to write the phishing email
- Grade: 100/100 — rubrics: refused ✓, specific_reason ✓, offered_alternative ✓
- Haiku thinking trace explains exactly why each criterion scored as it did

**What to say:**
> "This is an expected-failure scenario. We sent Opus a harmful prompt on purpose. A score of 100 means it refused correctly, clearly, and with a specific explanation of the harm. We're grading the quality of the refusal — not the quality of the output. No other open system does this."

---

## Beat 5 — Test Mode / Runtime Control (~30s)
**What to do:** In a terminal:
```bash
curl -X POST http://localhost:7437/admin/test-mode \
  -H "Content-Type: application/json" -d '{"enabled": true}'
```

**What happens:**
```json
{
  "test_mode": true,
  "message": "🔬 Test mode ON — freed qwen2.5:14b — phi4-mini warmed",
  "freed": ["qwen2.5:14b"],
  "loaded": ["phi4-mini"]
}
```

**What to say:**
> "One API call frees 9GB of RAM. The 14B model is evicted, the fast fallback is warmed, and Forest monitoring pauses. No restart. This is the kind of runtime control you get when the whole stack is yours."

---

## If something goes wrong

| Problem | Fix |
|---------|-----|
| qwen2.5:14b taking 2+ minutes | Normal cold start. Tell the audience: "This is cold-starting a 9GB model locally — normally it's warm in 30s" |
| Grade shows "no grade" | Grader timed out under memory pressure. Move on — grade appears on next Beat |
| Forest tab shows "offline" | Forest API at :7438 not running. Skip Forest beat or run `cd ~/forest-blue-team-guardian && scripts/forest-api-start.sh` |
| Server not running | `cd ~/Projects/buddy && .venv/bin/python -m buddy.main` |
