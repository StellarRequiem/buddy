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
}

// ── Chat ────────────────────────────────────────────────────────────────────
function appendMessage(role, content, model) {
  const box = document.getElementById('messages');
  const wrapper = document.createElement('div');
  wrapper.className = `msg ${role}`;

  const bubble = document.createElement('div');
  bubble.className = 'msg-bubble';
  bubble.textContent = content;

  const meta = document.createElement('div');
  meta.className = 'msg-meta';
  meta.textContent = model ? `via ${model}` : '';

  wrapper.appendChild(bubble);
  wrapper.appendChild(meta);
  box.appendChild(wrapper);
  box.scrollTop = box.scrollHeight;
  return wrapper;
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
    appendMessage('assistant', data.response, data.model_used);

    // Update model badge
    const badge = document.getElementById('model-badge');
    badge.textContent = data.model_used;
    badge.className = data.model_used === 'frontier' ? 'badge frontier' : 'badge';

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
