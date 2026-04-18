'use strict';

let currentSession = null;
let pendingShellCommand = null;

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

    currentSession = data.session_id;
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
