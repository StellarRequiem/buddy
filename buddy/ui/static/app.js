'use strict';

// ── Session persistence ─────────────────────────────────────────────────────
// Restore session from localStorage so a page refresh doesn't lose chat history.
let currentSession = localStorage.getItem('buddy_session') || null;
let pendingShellCommand = null;

function _saveSession(id) {
  currentSession = id;
  if (id) localStorage.setItem('buddy_session', id);
}

// ── Tab switching ───────────────────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.remove('hidden');
  document.querySelector(`.tab-btn[onclick="showTab('${name}')"]`).classList.add('active');

  if (name === 'tasks') htmx.trigger('#task-list', 'load');
  if (name === 'memory') {
    htmx.trigger('#facts-panel', 'load');
    htmx.trigger('#mem-stats', 'load');
  }
  if (name === 'forest') refreshForestStatus();
  if (name === 'demo') loadDemoScenarios();
}

// ── Chat ────────────────────────────────────────────────────────────────────
function appendMessage(role, content, model, grade) {
  const box = document.getElementById('messages');
  const wrapper = document.createElement('div');
  wrapper.className = `msg ${role}`;

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = content;
  wrapper.appendChild(bubble);

  // ── Grade panel (assistant messages only, when grade data available) ────────
  if (role === 'assistant' && grade && grade.composite_score !== undefined) {
    wrapper.appendChild(_buildGradePanel(grade, model));
  } else {
    const meta = document.createElement('div');
    meta.className = 'msg-meta';
    meta.textContent = model ? `via ${model}` : '';
    wrapper.appendChild(meta);
  }

  box.appendChild(wrapper);
  box.scrollTop = box.scrollHeight;
  return wrapper;
}

function _buildGradePanel(grade, model) {
  const score = grade.composite_score;
  const passed = grade.passed;
  const escalated = grade.escalated;
  const scoreClass = score >= 70 ? 'grade-pass' : score >= 50 ? 'grade-warn' : 'grade-fail';

  const panel = document.createElement('div');
  panel.className = 'grade-panel';

  // ── Score line ──────────────────────────────────────────────────────────────
  const scoreLine = document.createElement('div');
  scoreLine.className = 'grade-score-line';
  scoreLine.innerHTML =
    `<span class="grade-badge ${scoreClass}">● ${score.toFixed(0)} ${passed ? 'PASS' : 'FAIL'}</span>` +
    `<span class="grade-model">${model || ''}</span>` +
    (escalated ? `<span class="grade-escalated" title="Local fell below threshold">↑ escalated</span>` : '') +
    `<button class="grade-toggle" onclick="this.closest('.grade-panel').classList.toggle('expanded')">▸</button>`;
  panel.appendChild(scoreLine);

  // ── Rubric breakdown (collapsed by default) ─────────────────────────────────
  const detail = document.createElement('div');
  detail.className = 'grade-detail';

  if (grade.rubrics && grade.rubrics.length) {
    const rubricList = document.createElement('div');
    rubricList.className = 'grade-rubrics';
    for (const r of grade.rubrics) {
      const pct = r.score;
      const barClass = pct >= 70 ? 'bar-pass' : pct >= 40 ? 'bar-warn' : 'bar-fail';
      rubricList.innerHTML += `
        <div class="rubric-row">
          <span class="rubric-name">${r.name}</span>
          <div class="rubric-bar-wrap">
            <div class="rubric-bar ${barClass}" style="width:${pct}%"></div>
          </div>
          <span class="rubric-score">${pct.toFixed(0)}</span>
          <span class="rubric-weight">${(r.weight * 100).toFixed(0)}%</span>
        </div>`;
    }
    detail.appendChild(rubricList);
  }

  // ── Thinking trace (extended thinking — Haiku's reasoning) ──────────────────
  if (grade.thinking_trace && grade.thinking_trace.length > 0) {
    const thinkWrap = document.createElement('div');
    thinkWrap.className = 'thinking-trace';
    thinkWrap.innerHTML =
      `<div class="thinking-label">🧠 Grader reasoning (extended thinking)</div>` +
      `<pre class="thinking-text">${_escapeHtml(grade.thinking_trace)}</pre>`;
    detail.appendChild(thinkWrap);
  }

  panel.appendChild(detail);
  return panel;
}

function _escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showThinking() {
  const box = document.getElementById('messages');
  const el = document.createElement('div');
  el.className = 'thinking';
  el.id = 'thinking-indicator';
  el.textContent = '…';
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
}

function hideThinking() {
  const el = document.getElementById('thinking-indicator');
  if (el) el.remove();
}

async function sendMessage(e) {
  e.preventDefault();
  const input = document.getElementById('msg-input');
  const msg = input.value.trim();
  if (!msg) return;

  const frontier = document.getElementById('frontier-toggle').checked;
  input.value = '';
  document.getElementById('send-btn').disabled = true;

  appendMessage('user', msg, '');
  showThinking();

  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: msg,
        session_id: currentSession || '',
        force_frontier: frontier,
      }),
    });
    const data = await resp.json();
    hideThinking();

    _saveSession(data.session_id);
    appendMessage('assistant', data.response, data.model_used, data.grade);

    // Update model badge
    const badge = document.getElementById('model-badge');
    const isOpus = data.model_used && data.model_used.includes('opus');
    badge.textContent = isOpus ? 'opus 4.7' : data.model_used;
    badge.className = isOpus ? 'badge frontier' : 'badge';

    // Shell gate
    if (data.pending_confirmation) {
      showShellGate(data.pending_confirmation);
    }
  } catch (err) {
    hideThinking();
    appendMessage('assistant', `Error: ${err.message}`, '');
  } finally {
    document.getElementById('send-btn').disabled = false;
    input.focus();
  }
}

// ── Shell gate ──────────────────────────────────────────────────────────────
function showShellGate(confirmation) {
  pendingShellCommand = confirmation.command;
  document.getElementById('shell-cmd-preview').textContent = confirmation.command;
  document.getElementById('shell-gate').classList.remove('hidden');
}

async function approveShell() {
  if (!pendingShellCommand) return;
  document.getElementById('shell-gate').classList.add('hidden');
  showThinking();
  try {
    const resp = await fetch('/shell/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: pendingShellCommand, session_id: currentSession }),
    });
    const data = await resp.json();
    hideThinking();
    appendMessage('assistant', `$ ${pendingShellCommand}\n\n${data.output}`, 'shell');
  } catch (err) {
    hideThinking();
    appendMessage('assistant', `Shell error: ${err.message}`, '');
  }
  pendingShellCommand = null;
}

function denyShell() {
  document.getElementById('shell-gate').classList.add('hidden');
  appendMessage('assistant', `[Shell command blocked by user]`, 'shell');
  pendingShellCommand = null;
}

// ── Tasks ────────────────────────────────────────────────────────────────────

// htmx renders tasks — override with JSON for create
async function createTask() {
  const input = document.getElementById('task-input');
  const title = input.value.trim();
  if (!title) return;
  input.value = '';
  await fetch('/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });
  htmx.trigger('#task-list', 'load');
}

// htmx will GET /tasks and replace #task-list innerHTML
// but /tasks returns JSON — we need to transform it
document.addEventListener('htmx:beforeSwap', (e) => {
  if (e.detail.target.id === 'task-list') {
    try {
      const data = JSON.parse(e.detail.serverResponse);
      e.detail.serverResponse = renderTasks(data.tasks || []);
    } catch (_) {}
  }
  if (e.detail.target.id === 'facts-panel') {
    try {
      const data = JSON.parse(e.detail.serverResponse);
      e.detail.serverResponse = renderFacts(data.facts || {});
    } catch (_) {}
  }
  if (e.detail.target.id === 'mem-stats') {
    try {
      const data = JSON.parse(e.detail.serverResponse);
      e.detail.serverResponse = `<p>Vector chunks: ${data.vector_memory_chunks} &nbsp;|&nbsp; Facts: ${data.facts_count}</p>`;
    } catch (_) {}
  }
});

function renderTasks(tasks) {
  if (!tasks.length) return '<p style="color:var(--muted)">No tasks yet.</p>';
  return tasks.map(t => `
    <div class="task-item">
      <span class="task-status ${t.status}">${t.status}</span>
      <span>${t.title}</span>
    </div>`).join('');
}

// ── Memory ───────────────────────────────────────────────────────────────────
function renderFacts(facts) {
  const entries = Object.entries(facts);
  if (!entries.length) return '<p style="color:var(--muted)">No facts stored yet.</p>';
  return entries.map(([k, v]) => `
    <div class="fact-item">
      <span class="fact-key">${k}</span>
      <span class="fact-val">${v}</span>
    </div>`).join('');
}

// ── Forest status ───────────────────────────────────────────────────────────
async function refreshForestStatus() {
  const bar = document.getElementById('forest-panel');
  const detail = document.getElementById('forest-detail');

  try {
    const resp = await fetch('/forest/status');
    const d = await resp.json();

    if (d.status === 'offline') {
      if (bar)    bar.innerHTML = '🌲 Forest: <span class="forest-offline">offline</span>';
      if (detail) detail.innerHTML = `<p style="color:var(--muted)">Forest swarm not running.<br><code>${d.message || ''}</code></p>`;
      return;
    }
    if (d.status === 'error') {
      if (bar)    bar.innerHTML = `🌲 Forest: <span class="forest-offline">error</span>`;
      if (detail) detail.innerHTML = `<p style="color:var(--muted)">Error: ${d.message}</p>`;
      return;
    }

    const active = d.active_incidents || [];
    const sev = d.severity_breakdown || {};
    const critical = (sev['CRITICAL'] || 0) + (sev['ATTACK'] || 0);
    const badgeClass = critical > 0 ? 'forest-badge critical' : 'forest-badge ok';
    const badgeLabel = critical > 0 ? `⚠ ${critical} critical` : '✓ clear';

    // Header bar — compact
    if (bar) {
      bar.innerHTML = `🌲 Forest: <span class="${badgeClass}">${badgeLabel}</span>` +
        ` <span class="forest-stat">${d.total_logged} logged · ${d.chain_length} chain</span>`;
    }

    // Detail panel — full
    if (detail) {
      let html = `<div class="forest-stats-grid">
        <div class="forest-stat-card"><div class="stat-val">${d.total_logged}</div><div class="stat-lbl">incidents</div></div>
        <div class="forest-stat-card"><div class="stat-val">${d.chain_length}</div><div class="stat-lbl">chain entries</div></div>
        <div class="forest-stat-card"><div class="stat-val">${d.improvements_logged || 0}</div><div class="stat-lbl">improvements</div></div>
        <div class="forest-stat-card"><div class="stat-val ${critical > 0 ? 'critical-val' : ''}">${critical}</div><div class="stat-lbl">critical/attack</div></div>
      </div>`;

      if (Object.keys(sev).length) {
        html += '<h3>Severity breakdown</h3><div class="forest-sev-list">';
        for (const [k, v] of Object.entries(sev)) {
          html += `<span class="inc-sev ${k.toLowerCase()}">${k}: ${v}</span> `;
        }
        html += '</div>';
      }

      if (active.length) {
        html += '<h3>Active incidents</h3><div class="forest-incidents">';
        for (const inc of active) {
          const actions = inc.response_actions?.join(', ') || '—';
          const ips = inc.blocked_ips?.join(', ') || '—';
          html += `<div class="forest-inc-card">
            <div><span class="inc-sev ${inc.severity.toLowerCase()}">${inc.severity}</span>
              <span class="inc-type">${inc.threat_type}</span>
              <span class="inc-phase">[${inc.phase}]</span>
            </div>
            <div class="inc-meta">actions: ${actions}</div>
            <div class="inc-meta">blocked: ${ips}</div>
            <div class="inc-meta" style="color:var(--muted)">${inc.timestamp?.slice(0,19) || ''}</div>
          </div>`;
        }
        html += '</div>';
      } else {
        html += '<p style="color:var(--muted);margin-top:1rem">No active incidents.</p>';
      }

      html += `<p style="color:var(--muted);font-size:.75rem;margin-top:1rem">Last checked: ${d.checked_at?.slice(0,19) || 'unknown'} UTC</p>`;
      detail.innerHTML = html;
    }
  } catch (_) {
    if (bar)    bar.innerHTML = '🌲 Forest: <span class="forest-offline">unreachable</span>';
    if (detail) detail.innerHTML = '<p style="color:var(--muted)">Could not reach forest API.</p>';
  }
}

// Poll forest status every 30s and on load
document.addEventListener('DOMContentLoaded', () => {
  refreshForestStatus();
  setInterval(refreshForestStatus, 30_000);

  // If we restored a session, load its history
  if (currentSession) {
    fetch(`/chat/history/${currentSession}?limit=30`)
      .then(r => r.json())
      .then(d => {
        (d.messages || []).forEach(m => appendMessage(m.role, m.content, m.model || ''));
      })
      .catch(() => {}); // silently ignore if session no longer exists
  }
});

// ── Demo (expected-failure) ─────────────────────────────────────────────────
let _demoScenarios = [];

async function loadDemoScenarios() {
  const container = document.getElementById('demo-scenarios');
  if (!container) return;
  if (_demoScenarios.length) { _renderScenarios(); return; }
  try {
    const resp = await fetch('/demo/tasks');
    const d = await resp.json();
    _demoScenarios = d.scenarios || [];
    _renderScenarios();
  } catch (e) {
    container.innerHTML = `<p style="color:var(--muted)">Could not load demo scenarios: ${e.message}</p>`;
  }
}

function _renderScenarios() {
  const container = document.getElementById('demo-scenarios');
  if (!container) return;
  container.innerHTML = _demoScenarios.map(s => `
    <div class="demo-card" onclick="runDemo('${s.id}')">
      <div class="demo-card-label">${s.label}</div>
      <div class="demo-card-why">${s.why}</div>
      <button class="demo-run-btn">Run ▸</button>
    </div>`).join('');
}

async function runDemo(scenarioId) {
  const result = document.getElementById('demo-result');
  if (!result) return;

  // Show loading state
  result.className = 'demo-result';
  result.innerHTML = `
    <div class="demo-loading">
      <div class="demo-loading-title">Running demo…</div>
      <div class="demo-loading-steps">
        <div class="demo-step active">① Sending harmful prompt to Opus 4.7</div>
        <div class="demo-step">② Haiku grading refusal quality (extended thinking)</div>
        <div class="demo-step">③ Rendering results</div>
      </div>
    </div>`;

  try {
    const resp = await fetch('/demo/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario_id: scenarioId }),
    });
    const d = await resp.json();
    if (!resp.ok) {
      result.innerHTML = `<p style="color:var(--red)">Error: ${d.detail}</p>`;
      return;
    }
    _renderDemoResult(d);
  } catch (e) {
    result.innerHTML = `<p style="color:var(--red)">Error: ${e.message}</p>`;
  }
}

function _renderDemoResult(d) {
  const result = document.getElementById('demo-result');
  const g = d.grade;
  const scoreClass = g.composite_score >= 70 ? 'grade-pass' : g.composite_score >= 50 ? 'grade-warn' : 'grade-fail';

  let rubricHtml = (g.rubrics || []).map(r => {
    const pct = r.score;
    const barClass = pct >= 70 ? 'bar-pass' : pct >= 40 ? 'bar-warn' : 'bar-fail';
    return `<div class="rubric-row">
      <span class="rubric-name">${r.name.replace('_', ' ')}</span>
      <div class="rubric-bar-wrap"><div class="rubric-bar ${barClass}" style="width:${pct}%"></div></div>
      <span class="rubric-score">${pct.toFixed(0)}</span>
      <span class="rubric-weight">${(r.weight * 100).toFixed(0)}%</span>
    </div>`;
  }).join('');

  const thinkingHtml = g.thinking_trace ? `
    <div class="thinking-trace" style="margin-top:16px">
      <div class="thinking-label">🧠 Haiku's grader reasoning (extended thinking)</div>
      <pre class="thinking-text">${_escapeHtml(g.thinking_trace)}</pre>
    </div>` : '';

  result.className = 'demo-result';
  result.innerHTML = `
    <div class="demo-result-header">
      <span class="grade-badge ${scoreClass}">● ${g.composite_score.toFixed(0)}/100 ${g.passed ? 'PASS' : 'FAIL'}</span>
      <span class="demo-headline">${d.headline}</span>
    </div>

    <div class="demo-expected-failure-note">
      ⚡ <strong>Expected-failure semantics:</strong> this task expected the agent to refuse.
      A high score means it refused <em>correctly</em> — not that it produced good output.
    </div>

    <div class="demo-columns">
      <div class="demo-col">
        <h3>Harmful prompt sent</h3>
        <div class="demo-prompt">${_escapeHtml(d.scenario.prompt)}</div>
        <h3>Opus 4.7 response</h3>
        <div class="demo-response">${_escapeHtml(d.agent_response)}</div>
      </div>
      <div class="demo-col">
        <h3>Refusal quality rubric</h3>
        <div class="grade-rubrics">${rubricHtml}</div>
        ${thinkingHtml}
      </div>
    </div>

    <button class="demo-back-btn" onclick="document.getElementById('demo-result').classList.add('hidden');document.getElementById('demo-scenarios').style.display=''">
      ← Try another scenario
    </button>`;

  document.getElementById('demo-scenarios').style.display = 'none';
}

async function searchMemory() {
  const q = document.getElementById('mem-search-input').value.trim();
  if (!q) return;
  const resp = await fetch(`/memory/search?q=${encodeURIComponent(q)}&n=5`);
  const data = await resp.json();
  const container = document.getElementById('mem-results');
  container.innerHTML = (data.results || []).map(r => `
    <div class="mem-result-item">
      <div>${r.text}</div>
      <div class="mem-dist">distance: ${r.distance?.toFixed(3) ?? 'n/a'}</div>
    </div>`).join('') || '<p style="color:var(--muted)">No results.</p>';
}
