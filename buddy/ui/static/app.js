'use strict';

// ── Session persistence ─────────────────────────────────────────────────────
// Restore session from localStorage so a page refresh doesn't lose chat history.
let currentSession = localStorage.getItem('buddy_session') || null;
let pendingShellCommand = null;
let pendingShellToken = null;    // one-time CSRF token issued by the server

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

  // Clear Forest alert dot when landing on Chat tab
  if (name === 'chat') {
    const dot = document.querySelector('.tab-btn .alert-dot');
    if (dot) dot.remove();
  }

  if (name === 'tasks') htmx.trigger('#task-list', 'load');
  if (name === 'memory') {
    loadSessions();
    htmx.trigger('#facts-panel', 'load');
    htmx.trigger('#mem-stats', 'load');
  }
  if (name === 'tools') loadToolsTab();
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
    await _sendStreaming(msg, frontier);
  } catch (err) {
    hideThinking();
    appendMessage('assistant', `Error: ${err.message}`, '');
  } finally {
    document.getElementById('send-btn').disabled = false;
    input.focus();
  }
}

// ── Agent activity panel ─────────────────────────────────────────────────────
// ── qwen3 reasoning block ────────────────────────────────────────────────────
function _getOrCreateThinkPanel(bubbleWrapper) {
  let panel = bubbleWrapper.querySelector('.think-panel');
  if (!panel) {
    panel = document.createElement('div');
    // Open (not collapsed) while streaming so the user sees reasoning in real time
    panel.className = 'think-panel streaming';
    panel.innerHTML =
      `<div class="think-header" onclick="this.closest('.think-panel').classList.toggle('collapsed')">` +
      `<span class="think-icon">🧠</span>` +
      `<span class="think-label">` +
        `Reasoning<span class="think-dot"> …</span>` +
        `<span class="think-word-count" style="display:none"></span>` +
      `</span>` +
      `<span class="think-toggle">▸</span>` +
      `</div>` +
      `<div class="think-body-wrap"><pre class="think-body"></pre></div>`;
    // Insert before activity panel and bubble
    bubbleWrapper.insertBefore(panel, bubbleWrapper.firstChild);
  }
  return panel;
}

function _updateThinkPanel(panel, text) {
  if (!panel) return;
  const body = panel.querySelector('.think-body');
  if (!body) return;
  body.textContent = text;
  // Auto-scroll to bottom while streaming
  const wrap = panel.querySelector('.think-body-wrap');
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
}

function _finalizeThinkPanel(panel) {
  if (!panel) return;
  // End streaming state and collapse
  panel.classList.remove('streaming');
  panel.classList.add('collapsed');
  const body = panel.querySelector('.think-body');
  if (!body) return;
  const words = body.textContent.trim().split(/\s+/).filter(Boolean).length;
  const badge = panel.querySelector('.think-word-count');
  if (badge) {
    badge.textContent = `${words} words`;
    badge.style.display = '';
  }
}

function _getOrCreateActivityPanel(bubbleWrapper) {
  let panel = bubbleWrapper.querySelector('.agent-activity');
  if (!panel) {
    panel = document.createElement('div');
    panel.className = 'agent-activity';
    // Insert before the bubble text
    bubbleWrapper.insertBefore(panel, bubbleWrapper.querySelector('.msg-bubble'));
  }
  return panel;
}

function _addToolCallRow(panel, name, args) {
  const row = document.createElement('div');
  row.className = 'agent-tool-row pending';
  row.dataset.tool = name;
  const argsPreview = Object.keys(args).length
    ? Object.entries(args).slice(0, 2).map(([k, v]) => `${k}=${JSON.stringify(v).slice(0,40)}`).join(' ')
    : '';
  row.innerHTML =
    `<span class="agent-tool-icon">⚙</span>` +
    `<span class="agent-tool-name">${name}</span>` +
    (argsPreview ? `<span class="agent-tool-args">${_escapeHtml(argsPreview)}</span>` : '') +
    `<span class="agent-tool-status">…</span>`;
  panel.appendChild(row);
  return row;
}

function _resolveToolRow(panel, name, preview, full) {
  // Find the last pending row for this tool name
  const rows = [...panel.querySelectorAll(`.agent-tool-row.pending[data-tool="${name}"]`)];
  const row = rows[rows.length - 1];
  if (!row) return;
  row.classList.remove('pending');
  row.classList.add('done');
  const statusEl = row.querySelector('.agent-tool-status');
  if (statusEl) statusEl.textContent = preview ? `→ ${preview.slice(0, 80)}` : '✓';

  // Store full result and make row clickable to expand
  if (full && full !== preview) {
    row.dataset.full = full;
    row.title = 'Click to expand';
    row.style.cursor = 'pointer';
    row.addEventListener('click', () => {
      const existing = row.nextElementSibling;
      if (existing && existing.classList.contains('agent-tool-expanded')) {
        existing.remove();
        row.classList.remove('expanded');
      } else {
        const exp = document.createElement('div');
        exp.className = 'agent-tool-expanded';
        exp.textContent = full;
        row.after(exp);
        row.classList.add('expanded');
      }
    });
  }
}

function _finalizeActivityPanel(panel, toolsCount) {
  if (!panel || toolsCount === 0) {
    if (panel) panel.remove();
    return;
  }
  // Collapse button
  const header = document.createElement('div');
  header.className = 'agent-activity-header';
  header.innerHTML =
    `<span class="agent-summary">🔧 Used ${toolsCount} tool${toolsCount > 1 ? 's' : ''}</span>` +
    `<button class="agent-toggle" onclick="this.closest('.agent-activity').classList.toggle('collapsed')">▴</button>`;
  panel.insertBefore(header, panel.firstChild);
}

async function _sendStreaming(msg, frontier) {
  const resp = await fetch('/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: msg,
      session_id: currentSession || '',
      force_frontier: frontier,
    }),
  });

  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let bubbleWrapper = null;   // the streaming message wrapper
  let activityPanel = null;
  let thinkPanel = null;      // qwen3 reasoning block
  let thinkText = '';         // accumulated thinking_trace content
  let fullText = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      let event;
      try { event = JSON.parse(line.slice(6)); } catch { continue; }

      // ── qwen3 reasoning trace ────────────────────────────────────────────────
      if (event.type === 'thinking_trace') {
        if (!bubbleWrapper) {
          hideThinking();
          bubbleWrapper = appendMessage('assistant', '', '', null);
          bubbleWrapper.querySelector('.msg-bubble').classList.add('streaming');
        }
        thinkText += event.token;
        if (!thinkPanel) {
          thinkPanel = _getOrCreateThinkPanel(bubbleWrapper);
        }
        _updateThinkPanel(thinkPanel, thinkText);
        const box = document.getElementById('messages');
        box.scrollTop = box.scrollHeight;
        continue;
      }

      // ── Tool call start ──────────────────────────────────────────────────────
      if (event.type === 'tool_call') {
        if (!bubbleWrapper) {
          hideThinking();
          bubbleWrapper = appendMessage('assistant', '', '', null);
          bubbleWrapper.querySelector('.msg-bubble').classList.add('streaming');
        }
        activityPanel = _getOrCreateActivityPanel(bubbleWrapper);
        _addToolCallRow(activityPanel, event.name, event.args || {});
        const box = document.getElementById('messages');
        box.scrollTop = box.scrollHeight;
        continue;
      }

      // ── Tool result ──────────────────────────────────────────────────────────
      if (event.type === 'tool_result') {
        if (activityPanel) {
          _resolveToolRow(activityPanel, event.name, event.preview || '', event.full || '');
        }
        continue;
      }

      // ── Shell gate from agent ────────────────────────────────────────────────
      if (event.type === 'shell_gate') {
        // will be surfaced via the done payload's pending_confirmation
        continue;
      }

      // ── Token ────────────────────────────────────────────────────────────────
      if (event.token !== undefined) {
        if (!bubbleWrapper) {
          hideThinking();
          bubbleWrapper = appendMessage('assistant', '', '', null);
          bubbleWrapper.querySelector('.msg-bubble').classList.add('streaming');
        }
        fullText += event.token;
        bubbleWrapper.querySelector('.msg-bubble').textContent = fullText;
        const box = document.getElementById('messages');
        box.scrollTop = box.scrollHeight;
      }

      // ── Done ─────────────────────────────────────────────────────────────────
      if (event.done) {
        const toolsCount = event.tools_called || 0;

        if (bubbleWrapper) {
          bubbleWrapper.querySelector('.msg-bubble').classList.remove('streaming');
          bubbleWrapper.querySelector('.msg-bubble').textContent = fullText;

          // Finalize think panel (collapse, update line count)
          if (thinkPanel) _finalizeThinkPanel(thinkPanel);

          // Finalize activity panel
          if (activityPanel) {
            _finalizeActivityPanel(activityPanel, toolsCount);
          }

          // Remove placeholder meta
          const existingMeta = bubbleWrapper.querySelector('.msg-meta');
          if (existingMeta) existingMeta.remove();

          if (event.grade && event.grade.composite_score !== undefined) {
            bubbleWrapper.appendChild(_buildGradePanel(event.grade, event.model));
          } else {
            const meta = document.createElement('div');
            meta.className = 'msg-meta';
            meta.textContent =
              (event.model ? `via ${event.model}` : '') +
              (event.escalated ? ' · ↑ escalated' : '') +
              (toolsCount ? ` · ${toolsCount} tool${toolsCount > 1 ? 's' : ''}` : '');
            bubbleWrapper.appendChild(meta);
          }
        } else {
          hideThinking();
          bubbleWrapper = appendMessage('assistant', fullText, event.model || '', event.grade || null);
        }

        _saveSession(event.session_id);

        const badge = document.getElementById('model-badge');
        const isOpus = event.model && event.model.includes('opus');
        badge.textContent = isOpus ? 'opus 4.7' : (event.model || 'local');
        badge.className = isOpus ? 'badge frontier' : 'badge';

        if (event.pending_confirmation) {
          showShellGate(event.pending_confirmation);
        }
      }

      if (event.error) {
        hideThinking();
        appendMessage('assistant', `Error: ${event.error}`, '');
      }
    }
  }
}

// ── Shell gate ──────────────────────────────────────────────────────────────
function showShellGate(confirmation) {
  pendingShellCommand = confirmation.command;
  pendingShellToken   = confirmation.token || null;
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
      body: JSON.stringify({
        command: pendingShellCommand,
        session_id: currentSession,
        token: pendingShellToken || '',
      }),
    });
    const data = await resp.json();
    hideThinking();
    if (!resp.ok) {
      appendMessage('assistant', `Shell blocked: ${data.detail}`, 'shell');
    } else {
      appendMessage('assistant', `$ ${pendingShellCommand}\n\n${data.output}`, 'shell');
    }
  } catch (err) {
    hideThinking();
    appendMessage('assistant', `Shell error: ${err.message}`, '');
  }
  pendingShellCommand = null;
  pendingShellToken   = null;
}

function denyShell() {
  document.getElementById('shell-gate').classList.add('hidden');
  appendMessage('assistant', `[Shell command blocked by user]`, 'shell');
  pendingShellCommand = null;
  pendingShellToken   = null;
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

    if (d.status === 'paused') {
      if (bar)    bar.innerHTML = '🌲 Forest: <span class="forest-offline">paused</span>';
      if (detail) detail.innerHTML = `<p style="color:var(--muted)">🔬 Test mode active — monitoring paused.<br>Disable test mode to resume.</p>`;
      return;
    }
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

// ── Test mode banner ─────────────────────────────────────────────────────────
async function checkTestMode() {
  try {
    const resp = await fetch('/admin/status');
    const d = await resp.json();
    const banner = document.getElementById('test-mode-banner');
    if (banner) banner.classList.toggle('hidden', !d.test_mode);
  } catch (_) {}
}

async function disableTestMode() {
  await fetch('/admin/test-mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: false }),
  });
  document.getElementById('test-mode-banner')?.classList.add('hidden');
  refreshForestStatus();
}

// ── Forest scan scheduler ────────────────────────────────────────────────────
let _forestInterval = null;

function scheduleForestScan(intervalMs) {
  if (_forestInterval) clearInterval(_forestInterval);
  if (intervalMs && intervalMs > 0) {
    _forestInterval = setInterval(refreshForestStatus, intervalMs);
    return true;
  }
  _forestInterval = null;
  return false;
}

function stopForestScan() {
  if (_forestInterval) clearInterval(_forestInterval);
  _forestInterval = null;
}

function onForestIntervalChange(val) {
  const ms = parseInt(val, 10);
  const label = document.getElementById('forest-scan-label');
  if (ms > 0) {
    scheduleForestScan(ms);
    refreshForestStatus();
    if (label) label.textContent = `Forest blue-team swarm · auto-scan every ${ms >= 60000 ? ms/60000+'m' : ms/1000+'s'}`;
  } else {
    stopForestScan();
    if (label) label.textContent = 'Forest blue-team swarm · manual scan';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  checkTestMode();
  connectAlertStream();
  // No auto-poll on startup — Forest is manual-scan only.
  // Tab click already calls refreshForestStatus() via showTab().
  // Call scheduleForestScan(ms) from the console to automate.

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

// ── Forest alert stream ──────────────────────────────────────────────────────
let _alertSource = null;

function connectAlertStream() {
  if (_alertSource) _alertSource.close();
  _alertSource = new EventSource('/alerts/stream');

  _alertSource.onmessage = (e) => {
    let alert;
    try { alert = JSON.parse(e.data); } catch { return; }
    if (alert.type === 'forest_alert') _onForestAlert(alert);
  };

  // Auto-reconnect is handled by EventSource natively.
  // Log errors to console only (don't surface to user — Forest may just be offline).
  _alertSource.onerror = () => {};
}

function _onForestAlert(alert) {
  const sevClass = alert.severity === 'ATTACK' ? 'attack' : 'critical';
  const actions = alert.response_actions?.join(', ') || 'none';
  const ips     = alert.blocked_ips?.join(', ') || 'none';
  const ts      = alert.timestamp?.slice(0, 19) || '';

  const summary =
    `⚠ Forest ${alert.severity}: ${alert.threat_type}\n` +
    `Phase: ${alert.phase || '—'}  |  Actions: ${actions}  |  Blocked IPs: ${ips}\n` +
    (ts ? `Detected: ${ts} UTC` : '');

  // Append a red system bubble in chat
  const box  = document.getElementById('messages');
  const wrap = document.createElement('div');
  wrap.className = 'msg forest-alert';
  const bub = document.createElement('div');
  bub.className = 'msg-bubble';
  bub.textContent = summary;
  const meta = document.createElement('div');
  meta.className = 'msg-meta';
  meta.textContent = 'Forest Blue-Team · live alert';
  wrap.appendChild(bub);
  wrap.appendChild(meta);
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;

  // Pulse the Chat tab button if we're not already on it
  const chatBtn = document.querySelector('.tab-btn[onclick="showTab(\'chat\')"]');
  if (chatBtn && !chatBtn.classList.contains('active')) {
    if (!chatBtn.querySelector('.alert-dot')) {
      const dot = document.createElement('span');
      dot.className = 'alert-dot';
      chatBtn.appendChild(dot);
    }
  }

  // Update the Forest status bar immediately
  const bar = document.getElementById('forest-panel');
  if (bar) {
    bar.innerHTML = `🌲 Forest: <span class="forest-badge critical">⚠ ALERT — ${alert.severity}</span>`;
  }
}

// ── Session dashboard ────────────────────────────────────────────────────────
async function loadSessions() {
  const container = document.getElementById('sessions-list');
  if (!container) return;
  try {
    const resp = await fetch('/chat/sessions');
    const data = await resp.json();
    const sessions = (data.sessions || []).slice().reverse(); // newest first

    if (!sessions.length) {
      container.innerHTML = '<p style="color:var(--muted)">No sessions yet.</p>';
      return;
    }

    container.innerHTML = sessions.map(id => {
      const isActive = id === currentSession;
      return `
        <div class="session-item${isActive ? ' active-session' : ''}" onclick="loadSession('${id}')">
          <span class="session-id">${id.slice(0, 12)}…</span>
          ${isActive ? '<span class="session-meta">current</span>' : ''}
          <button class="session-load-btn" onclick="event.stopPropagation();loadSession('${id}')">
            Load →
          </button>
        </div>`;
    }).join('');
  } catch (e) {
    if (container) container.innerHTML = `<p style="color:var(--muted)">Could not load sessions: ${e.message}</p>`;
  }
}

async function loadSession(sessionId) {
  const resp = await fetch(`/chat/history/${sessionId}?limit=50`);
  const data = await resp.json();

  // Clear current chat and render the selected session
  const box = document.getElementById('messages');
  box.innerHTML = '';
  (data.messages || []).forEach(m => appendMessage(m.role, m.content, m.model || ''));

  _saveSession(sessionId);

  // Remove alert dot from Chat tab if present
  const dot = document.querySelector('.tab-btn .alert-dot');
  if (dot) dot.remove();

  showTab('chat');
}

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

// ── Tool metrics panel ────────────────────────────────────────────────────────
async function loadToolMetrics() {
  const panel = document.getElementById('tool-metrics-panel');
  if (!panel) return;
  panel.textContent = 'Loading…';
  try {
    const resp = await fetch('/admin/tool-metrics');
    if (!resp.ok) { panel.textContent = 'No data (admin endpoint may require token).'; return; }
    const data = await resp.json();
    const agg = data.aggregate || [];
    if (!agg.length) { panel.textContent = 'No tool calls recorded yet.'; return; }

    panel.innerHTML = `
      <table class="metrics-table">
        <thead><tr>
          <th>Tool</th><th>Calls</th><th>Success</th><th>Avg ms</th><th>Last used</th>
        </tr></thead>
        <tbody>
          ${agg.map(r => {
            const pct = r.calls ? Math.round((r.successes / r.calls) * 100) : 0;
            const cls = pct >= 90 ? 'grade-pass' : pct >= 60 ? 'grade-warn' : 'grade-fail';
            return `<tr>
              <td class="metrics-name">${r.tool_name}</td>
              <td>${r.calls}</td>
              <td><span class="${cls}">${pct}%</span></td>
              <td>${r.avg_ms ?? '—'}</td>
              <td style="color:var(--muted);font-size:10px">${(r.last_used||'').slice(0,16)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    panel.textContent = `Error: ${e.message}`;
  }
}

// ── Session export ────────────────────────────────────────────────────────────
function exportSession() {
  if (!currentSession) {
    alert('No active session to export.');
    return;
  }
  const url = `/chat/export/${currentSession}`;
  const a = document.createElement('a');
  a.href = url;
  a.download = `buddy-${currentSession.slice(0, 8)}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// ── Tools tab ─────────────────────────────────────────────────────────────────
let _toolsData = [];   // full tool list from server
let _toolsTabLoaded = false;

async function loadToolsTab(force = false) {
  if (_toolsTabLoaded && !force) return;
  const grid = document.getElementById('tools-tab-grid');
  if (!grid) return;
  grid.textContent = 'Loading…';
  try {
    const resp = await fetch('/memory/tools');
    const data = await resp.json();
    _toolsData = data.tools || [];
    _toolsTabLoaded = true;
    _renderToolsGrid(_toolsData);
    _populateToolTestSelect(_toolsData);
    loadToolMetrics();   // auto-load metrics whenever catalogue loads
  } catch (e) {
    grid.innerHTML = `<p style="color:var(--muted)">Could not load tools: ${e.message}</p>`;
  }
}

function _renderToolsGrid(tools) {
  const grid = document.getElementById('tools-tab-grid');
  const countEl = document.getElementById('tools-tab-count');
  if (!grid) return;

  const showDisabled = document.getElementById('tools-show-disabled')?.checked ?? true;
  const filterVal = (document.getElementById('tools-filter')?.value || '').toLowerCase();

  const visible = tools.filter(t => {
    if (!showDisabled && t.disabled) return false;
    if (filterVal && !t.name.toLowerCase().includes(filterVal) &&
        !t.description.toLowerCase().includes(filterVal)) return false;
    return true;
  });

  if (countEl) countEl.textContent = `${visible.length} / ${tools.length}`;

  if (!visible.length) {
    grid.innerHTML = `<p style="color:var(--muted)">No tools match the filter.</p>`;
    return;
  }

  grid.innerHTML = visible.map(t => {
    const gateTag = t.human_gate
      ? `<span class="tool-gate-badge">⏸ approval</span>` : '';
    const params = t.parameters.map(p =>
      `<span class="tool-param${p.required ? ' required' : ''}" title="${p.description || ''}">${p.name}</span>`
    ).join(' ');
    const disabledChecked = t.disabled ? 'checked' : '';
    return `
      <div class="tool-card${t.disabled ? ' tool-disabled' : ''}" id="tc-${t.name}">
        <div class="tool-card-header">
          <span class="tool-card-name">${t.name}</span>
          ${gateTag}
          ${params ? `<span class="tool-card-params">${params}</span>` : ''}
          <label class="tool-toggle-label" title="${t.disabled ? 'Enable tool' : 'Disable tool'}">
            <input type="checkbox" class="tool-toggle-cb" ${disabledChecked}
              onchange="toggleTool('${t.name}', this.checked)"
              onclick="event.stopPropagation()">
            <span class="tool-toggle-track"></span>
          </label>
        </div>
        <div class="tool-card-desc">${_escapeHtml(t.description)}</div>
        <button class="tool-test-btn" onclick="quickTestTool('${t.name}')">Test ▶</button>
      </div>`;
  }).join('');
}

function filterTools(val) {
  if (_toolsData.length) _renderToolsGrid(_toolsData);
}

async function toggleTool(name, disabledChecked) {
  try {
    const resp = await fetch(`/admin/tools/${encodeURIComponent(name)}/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ disabled: disabledChecked }),
    });
    const data = await resp.json();
    // Update local cache
    const t = _toolsData.find(x => x.name === name);
    if (t) t.disabled = data.disabled;
    // Refresh the card
    const card = document.getElementById(`tc-${name}`);
    if (card) card.classList.toggle('tool-disabled', data.disabled);
  } catch (e) {
    alert(`Toggle failed: ${e.message}`);
    // Revert checkbox
    await loadToolsTab(true);
  }
}

// ── Tool test runner ─────────────────────────────────────────────────────────

function _populateToolTestSelect(tools) {
  const sel = document.getElementById('tool-test-select');
  if (!sel) return;
  sel.innerHTML = '<option value="">— select a tool —</option>' +
    tools.map(t => `<option value="${t.name}"${t.disabled ? ' disabled' : ''}>${t.name}</option>`).join('');
}

function onToolTestSelect(name) {
  const paramsDiv = document.getElementById('tool-test-params');
  const actionsDiv = document.getElementById('tool-test-actions');
  const outputEl = document.getElementById('tool-test-output');
  if (!paramsDiv) return;
  if (outputEl) { outputEl.textContent = ''; outputEl.classList.add('hidden'); }
  const elapsed = document.getElementById('tool-test-elapsed');
  if (elapsed) elapsed.textContent = '';

  if (!name) {
    paramsDiv.innerHTML = '';
    actionsDiv?.classList.add('hidden');
    return;
  }
  const tool = _toolsData.find(t => t.name === name);
  if (!tool) return;

  // Build a simple form for each parameter
  if (tool.parameters.length === 0) {
    paramsDiv.innerHTML = `<span class="tool-test-no-params">No parameters required.</span>`;
  } else {
    paramsDiv.innerHTML = tool.parameters.map(p => `
      <div class="tool-test-param-row">
        <label class="tool-test-param-label">
          ${p.name}${p.required ? '<span class="required-star">*</span>' : ''}
          <span class="tool-test-param-type">${p.type || 'any'}</span>
        </label>
        <input id="ttp-${p.name}" class="tool-test-param-input" type="text"
          placeholder="${_escapeHtml(p.description || p.name)}"
          data-param="${p.name}" data-type="${p.type || 'string'}">
      </div>`).join('');
  }
  actionsDiv?.classList.remove('hidden');
}

async function runToolTest() {
  const name = document.getElementById('tool-test-select')?.value;
  if (!name) return;

  const tool = _toolsData.find(t => t.name === name);
  const args = {};
  if (tool) {
    for (const p of tool.parameters) {
      const el = document.getElementById(`ttp-${p.name}`);
      if (el && el.value !== '') {
        const val = el.value;
        // Coerce to number if type says so
        if (p.type === 'integer' || p.type === 'number') {
          args[p.name] = Number(val);
        } else if (p.type === 'boolean') {
          args[p.name] = val === 'true' || val === '1';
        } else {
          args[p.name] = val;
        }
      }
    }
  }

  const outputEl = document.getElementById('tool-test-output');
  const elapsed = document.getElementById('tool-test-elapsed');
  if (outputEl) { outputEl.textContent = 'Running…'; outputEl.classList.remove('hidden'); }
  if (elapsed) elapsed.textContent = '';

  try {
    const resp = await fetch('/admin/tools/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tool_name: name, args }),
    });
    const data = await resp.json();
    if (outputEl) {
      outputEl.textContent = data.result || '(empty result)';
      outputEl.className = `tool-test-output ${data.ok ? 'test-ok' : 'test-fail'}`;
    }
    if (elapsed) elapsed.textContent = `${data.elapsed_ms}ms`;
  } catch (e) {
    if (outputEl) {
      outputEl.textContent = `Error: ${e.message}`;
      outputEl.className = 'tool-test-output test-fail';
    }
  }
}

// Quick-test button on a card (no-arg tools only)
function quickTestTool(name) {
  const sel = document.getElementById('tool-test-select');
  if (sel) { sel.value = name; onToolTestSelect(name); }
  // Scroll to test runner
  document.querySelector('.tool-test-panel')?.scrollIntoView({ behavior: 'smooth' });
  runToolTest();
}

// Legacy shim — Memory tab no longer has tools panel, but keep function name safe
function loadToolsPanel() {}

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
