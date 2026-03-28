// frontend/app.js — v3 FINAL + SECURITY PATCH
// FIXES:
//   1. handleInput() now properly called from textarea (was autoResize only)
//   2. char counter now renders (HTML element exists)
//   3. DevTools blocked: Ctrl+Shift+I, Ctrl+Shift+J, Ctrl+U, F12, right-click
//   4. Mobile panel backdrop/toggle wired correctly
//   5. All existing functionality preserved — no regressions

const API_BASE    = "http://localhost:8000";
const MAX_CHARS   = 1000;   // hard cap — matches maxlength on textarea
const RATE_LIMIT  = 3;      // max queries per window
const RATE_WINDOW = 10000;  // ms — 3 queries per 10 seconds

// ── SESSION STATE ──────────────────────────────────────────
let conversationHistory = [];
let sessionQueryCount   = 0;
let sessionTotalTime    = 0;
let sessionTotalConf    = 0;
let isLoading           = false;

// ── RATE LIMITING STATE ────────────────────────────────────
let queryTimestamps = [];  // rolling window of query times

// ══════════════════════════════════════════════════════════
// SECURITY — DevTools / inspect blocking
// WHY: prevents casual inspection of API calls and prompts
// during demo evaluation. Not bulletproof but adds friction.
// ══════════════════════════════════════════════════════════

// Block right-click context menu
document.addEventListener("contextmenu", function (e) {
  e.preventDefault();
  return false;
});

// Block keyboard shortcuts that open DevTools
document.addEventListener("keydown", function (e) {
  // F12
  if (e.key === "F12") {
    e.preventDefault();
    return false;
  }
  // Ctrl+Shift+I  (DevTools)
  // Ctrl+Shift+J  (Console)
  // Ctrl+Shift+C  (Inspector)
  // Ctrl+U        (View Source)
  if (
    e.ctrlKey &&
    e.shiftKey &&
    (e.key === "I" || e.key === "i" ||
     e.key === "J" || e.key === "j" ||
     e.key === "C" || e.key === "c")
  ) {
    e.preventDefault();
    return false;
  }
  if (e.ctrlKey && (e.key === "U" || e.key === "u")) {
    e.preventDefault();
    return false;
  }

  // NOTE: Escape and Enter handling happens in handleKeyDown()
  // below — do NOT block them here.
});

// Detect DevTools open via window size delta (Chrome/Edge heuristic)
// Shows a warning overlay if inspector panel is open
(function detectDevTools() {
  const threshold = 160;
  setInterval(function () {
    const widthDelta  = window.outerWidth  - window.innerWidth;
    const heightDelta = window.outerHeight - window.innerHeight;
    if (widthDelta > threshold || heightDelta > threshold) {
      // DevTools likely open — show subtle deterrent (not an error, just a notice)
      if (!document.getElementById("devtools-warning")) {
        const el = document.createElement("div");
        el.id = "devtools-warning";
        el.style.cssText = [
          "position:fixed", "top:0", "left:0", "width:100%",
          "padding:6px 16px", "background:#ff4444",
          "color:#fff", "font-family:monospace",
          "font-size:11px", "letter-spacing:2px",
          "text-align:center", "z-index:9999",
          "pointer-events:none"
        ].join(";");
        el.textContent = "⚠  DEVELOPER TOOLS DETECTED  ⚠";
        document.body.appendChild(el);
      }
    } else {
      const el = document.getElementById("devtools-warning");
      if (el) el.remove();
    }
  }, 1000);
})();

// ── INIT ───────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  checkHealth();
  setInterval(checkHealth, 30000);
  document.getElementById("question-input").focus();
  // Initialise char counter display
  updateCharCounter(0);
});

// ── HEALTH CHECK ───────────────────────────────────────────
async function checkHealth() {
  try {
    const res  = await fetch(`${API_BASE}/health`);
    const data = await res.json();

    const dotIndex  = document.getElementById("dot-index");
    const dotModel  = document.getElementById("dot-model");
    const valIndex  = document.getElementById("val-index");
    const valModel  = document.getElementById("val-model");
    const valChunks = document.getElementById("val-chunks");

    if (data.index_loaded) {
      dotIndex.className   = "status-dot dot-active";
      valIndex.textContent = "ONLINE";
    } else {
      dotIndex.className   = "status-dot dot-error";
      valIndex.textContent = "OFFLINE";
    }
    if (data.model_loaded) {
      dotModel.className   = "status-dot dot-active";
      valModel.textContent = "LOADED";
    } else {
      dotModel.className   = "status-dot dot-warning";
      valModel.textContent = "LOADING";
    }
    valChunks.textContent = data.total_chunks
      ? data.total_chunks.toLocaleString() : "—";

  } catch (err) {
    document.getElementById("dot-index").className   = "status-dot dot-error";
    document.getElementById("val-index").textContent = "NO CONN";
  }
}

// ── INPUT HANDLER — char counter + resize ─────────────────
// FIX: textarea now calls handleInput(this) instead of autoResize(this)
// This was the root cause of the char counter never updating.
function handleInput(textarea) {
  autoResize(textarea);
  updateCharCounter(textarea.value.length);

  // Visual warning on textarea itself when near limit
  if (textarea.value.length >= MAX_CHARS) {
    textarea.classList.add("at-limit");
  } else {
    textarea.classList.remove("at-limit");
  }
}

function updateCharCounter(len) {
  const el = document.getElementById("char-counter");
  if (!el) return;
  el.textContent = `${len} / ${MAX_CHARS}`;
  el.className   = len > MAX_CHARS * 0.9 ? "char-counter limit"
                 : len > MAX_CHARS * 0.7 ? "char-counter warn"
                 : "char-counter";
}

// ── RATE LIMIT CHECK ───────────────────────────────────────
function isRateLimited() {
  const now = Date.now();
  // Remove timestamps outside the window
  queryTimestamps = queryTimestamps.filter(t => now - t < RATE_WINDOW);
  if (queryTimestamps.length >= RATE_LIMIT) {
    const waitMs  = RATE_WINDOW - (now - queryTimestamps[0]);
    const waitSec = Math.ceil(waitMs / 1000);
    appendErrorMessage(
      `Rate limit: max ${RATE_LIMIT} queries per ${RATE_WINDOW/1000}s. `
      + `Wait ${waitSec}s.`
    );
    return true;
  }
  queryTimestamps.push(now);
  return false;
}

// ── INPUT SANITIZATION ─────────────────────────────────────
function sanitizeInput(text) {
  // Trim whitespace
  let clean = text.trim();
  // Hard length cap (belt-and-suspenders on top of maxlength attr)
  if (clean.length > MAX_CHARS) {
    clean = clean.slice(0, MAX_CHARS);
  }
  // Remove null bytes and control chars except newlines/tabs
  clean = clean.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, "");
  return clean;
}

// ── SEND QUESTION ──────────────────────────────────────────
async function sendQuestion() {
  const input    = document.getElementById("question-input");
  const raw      = input.value;
  const question = sanitizeInput(raw);

  if (!question || isLoading) return;

  // Frontend rate limiting
  if (isRateLimited()) return;

  // Empty after sanitization
  if (question.length < 3) {
    appendErrorMessage("Query too short. Please enter a meaningful question.");
    return;
  }

  input.value = "";
  autoResize(input);
  updateCharCounter(0);
  input.classList.remove("at-limit");
  appendUserMessage(question);
  closeAllPanels();  // close mobile panels when query sent

  const typingId = appendTyping();
  setLoading(true);

  try {
    const controller = new AbortController();
    const timeoutId  = setTimeout(() => controller.abort(), 30000); // 30s timeout

    const res = await fetch(`${API_BASE}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: question,
        conversation_history: conversationHistory.slice(-6)
      }),
      signal: controller.signal
    });

    clearTimeout(timeoutId);
    const data = await res.json();
    removeTyping(typingId);

    if (!res.ok) {
      appendErrorMessage(data.detail || "Server error. Please try again.");
      return;
    }

    appendAIMessage(data);
    renderSources(data.sources);

    if (data.expanded_query && data.expanded_query !== question) {
      appendExpansionBanner(data.expanded_query);
    }

    conversationHistory.push({ role: "user",      content: question    });
    conversationHistory.push({ role: "assistant", content: data.answer });

    updateStats(data.query_time_ms, data.confidence);

  } catch (err) {
    removeTyping(typingId);
    if (err.name === "AbortError") {
      appendErrorMessage("Request timed out after 30s. Try a shorter question.");
    } else {
      appendErrorMessage("Connection failed. Is the server running?");
    }
  } finally {
    setLoading(false);
    input.focus();
  }
}

// ── MESSAGE RENDERERS ──────────────────────────────────────
function appendUserMessage(text) {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "message user-message";
  // escapeHtml used for all user content — XSS prevention
  div.innerHTML = `
    <div class="message-icon">◈</div>
    <div class="message-body">
      <div class="message-meta">OPERATOR QUERY · ${timestamp()}</div>
      <div class="message-text">${escapeHtml(text)}</div>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
}

function appendAIMessage(data) {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "message ai-message";

  const confPct   = Math.round(data.confidence * 100);
  const confClass = data.confidence > 0.75 ? "conf-high"
                  : data.confidence > 0.50 ? "conf-medium" : "conf-low";
  const confLabel = data.confidence > 0.75 ? "HIGH CONF"
                  : data.confidence > 0.50 ? "MED CONF" : "LOW CONF";

  const hopCount = (data.sources || []).filter(s => s.hop === 2).length;
  const hopBadge = hopCount > 0
    ? `<span class="hop-badge">⇄ MULTI-HOP ${hopCount} XREF</span>` : "";

  // AI answer: escapeHtml then restore newlines as <br> for readability
  const safeAnswer = escapeHtml(data.answer).replace(/\n/g, "<br>");

  div.innerHTML = `
    <div class="message-icon">⬡</div>
    <div class="message-body">
      <div class="message-meta">
        <span>AI RESPONSE · ${timestamp()}</span>
        <span class="conf-badge ${confClass}">${confLabel} ${confPct}%</span>
        <span class="time-badge">${data.query_time_ms.toFixed(0)}ms</span>
        ${hopBadge}
      </div>
      <div class="message-text">${safeAnswer}</div>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
}

function appendExpansionBanner(expandedQuery) {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "message system-message expansion-banner";
  div.innerHTML = `
    <div class="message-icon" style="color:var(--amber);font-size:13px;">⇌</div>
    <div class="message-body">
      <div class="message-meta" style="color:var(--amber-dim)">ACRONYM EXPANSION APPLIED</div>
      <div class="message-text" style="font-size:11px;color:var(--text-dim);">
        Searched as: <em style="color:var(--amber)">${escapeHtml(expandedQuery)}</em>
      </div>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
}

function appendErrorMessage(text) {
  const container = document.getElementById("chat-messages");
  const div = document.createElement("div");
  div.className = "message system-message";
  div.style.borderColor = "rgba(255,68,68,0.3)";
  div.innerHTML = `
    <div class="message-icon" style="color:var(--red)">⚠</div>
    <div class="message-body">
      <div class="message-meta" style="color:var(--red)">ERROR</div>
      <div class="message-text">${escapeHtml(text)}</div>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
}

function appendTyping() {
  const container = document.getElementById("chat-messages");
  const id  = "typing-" + Date.now();
  const div = document.createElement("div");
  div.id = id;
  div.className = "message typing-message";
  div.innerHTML = `
    <div class="message-icon" style="color:var(--green)">⬡</div>
    <div class="message-body">
      <div class="message-meta">PROCESSING QUERY...</div>
      <div class="typing-dots"><span></span><span></span><span></span></div>
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
  return id;
}

function removeTyping(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

// ── SOURCES PANEL ──────────────────────────────────────────
function renderSources(sources) {
  const panel = document.getElementById("sources-panel");
  if (!sources || sources.length === 0) {
    panel.innerHTML = `<div class="no-sources">No sources retrieved.</div>`;
    return;
  }

  panel.innerHTML = sources.map((src, i) => {
    const scoreWidth = (src.relevance_score * 100).toFixed(0);
    const scoreLabel = (src.relevance_score * 100).toFixed(1) + "%";

    let typeBadge = "";
    if (src.chunk_type === "table") {
      typeBadge = `<span class="source-type-badge badge-table">TABLE</span>`;
    } else if (src.chunk_type === "figure") {
      typeBadge = `<span class="source-type-badge badge-figure">FIGURE</span>`;
    } else if (src.hop === 2) {
      typeBadge = `<span class="source-type-badge badge-xref">⇄ CROSS-REF</span>`;
    }

    const parentLine = src.parent_section
      ? `<div class="source-parent">PARENT: §${escapeHtml(src.parent_section)}</div>` : "";
    const paraLine = src.paragraph_hint
      ? `<div class="source-para">↳ ${escapeHtml(src.paragraph_hint)}</div>` : "";

    return `
    <div class="source-card ${src.hop === 2 ? 'source-card-xref' : ''}">
      <div class="source-rank-row">
        <span class="source-rank">SOURCE ${i + 1} OF ${sources.length}</span>
        ${typeBadge}
      </div>
      <div class="source-section">${escapeHtml(src.section_title)}</div>
      ${parentLine}
      <div class="source-page">PAGE ${src.page_number}</div>
      ${paraLine}
      <div class="source-score-row">
        <div class="source-score">
          <div class="source-score-fill" style="width:${scoreWidth}%"></div>
        </div>
        <span class="source-score-label">${scoreLabel}</span>
      </div>
      <div class="source-preview">${escapeHtml(src.text_preview)}</div>
    </div>`;
  }).join("");
}

// ── RESPONSIVE PANEL CONTROLS ──────────────────────────────
function togglePanel(side) {
  const panel    = document.getElementById(`${side}-panel`);
  const backdrop = document.getElementById("panel-backdrop");
  const isOpen   = panel.classList.contains("open");

  closeAllPanels();  // close any already-open panel first

  if (!isOpen) {
    panel.classList.add("open");
    backdrop.classList.add("active");
  }
}

function closeAllPanels() {
  document.getElementById("left-panel")?.classList.remove("open");
  document.getElementById("right-panel")?.classList.remove("open");
  document.getElementById("panel-backdrop")?.classList.remove("active");
}

// ── CLEAR HISTORY ──────────────────────────────────────────
function clearHistory() {
  conversationHistory = [];
  sessionQueryCount   = 0;
  sessionTotalTime    = 0;
  sessionTotalConf    = 0;
  queryTimestamps     = [];
  updateStatsDisplay();
  updateCharCounter(0);

  document.getElementById("sources-panel").innerHTML =
    `<div class="no-sources">Sources will appear here after your first query.</div>`;

  document.getElementById("chat-messages").innerHTML = `
    <div class="message system-message">
      <div class="message-icon">⬡</div>
      <div class="message-body">
        <div class="message-meta">HISTORY CLEARED · ${timestamp()}</div>
        <div class="message-text">
          Session history cleared. Knowledge base remains loaded.<br/>
          Ready for new queries.
        </div>
      </div>
    </div>
  `;
}

// ── EXIT SYSTEM ────────────────────────────────────────────
function exitSystem()     { document.getElementById("exit-modal").classList.add("active"); }
function closeExitModal() { document.getElementById("exit-modal").classList.remove("active"); }

async function confirmExit() {
  try { await fetch(`${API_BASE}/shutdown`, { method: "POST" }); } catch (e) {}
  window.close();
  document.body.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:center;
      height:100vh;background:#050810;color:#f5a623;
      font-family:'Share Tech Mono',monospace;font-size:14px;
      letter-spacing:3px;text-align:center;line-height:2;">
      SERVER SHUTDOWN INITIATED<br/>
      <span style="color:#7a8fab;font-size:11px;">You may close this tab.</span>
    </div>`;
}

document.addEventListener("click", e => {
  if (e.target === document.getElementById("exit-modal")) closeExitModal();
});

// ── KEYBOARD SHORTCUTS ─────────────────────────────────────
// NOTE: F12 / Ctrl+Shift+I blocking is handled in the security
// listener above. This handler only manages app-level shortcuts.
function handleKeyDown(e) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendQuestion();
  }
  if (e.key === "Escape") {
    closeExitModal();
    closeAllPanels();
  }
}

// ── SAMPLE QUERY INJECT ────────────────────────────────────
function injectQuery(text) {
  const input = document.getElementById("question-input");
  input.value = text;
  autoResize(input);
  updateCharCounter(text.length);
  input.focus();
  closeAllPanels();  // close mobile panel after selection
}

// ── HELPERS ────────────────────────────────────────────────
function setLoading(state) {
  isLoading = state;
  const btn   = document.getElementById("btn-send");
  const input = document.getElementById("question-input");
  btn.disabled    = state;
  input.disabled  = state;
  btn.textContent = state ? "..." : "TRANSMIT";
}

function scrollToBottom() {
  const c = document.getElementById("chat-messages");
  c.scrollTop = c.scrollHeight;
}

function autoResize(t) {
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 120) + "px";
}

function timestamp() {
  return new Date().toLocaleTimeString("en-US",
    { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

// SECURITY: strict HTML escaping — prevents XSS via API responses
function escapeHtml(str) {
  return String(str)
    .replace(/&/g,  "&amp;")
    .replace(/</g,  "&lt;")
    .replace(/>/g,  "&gt;")
    .replace(/"/g,  "&quot;")
    .replace(/'/g,  "&#x27;")
    .replace(/\//g, "&#x2F;");
}

function updateStats(queryTime, confidence) {
  sessionQueryCount++;
  sessionTotalTime += queryTime;
  sessionTotalConf += confidence;
  updateStatsDisplay();
}

function updateStatsDisplay() {
  document.getElementById("stat-queries").textContent = sessionQueryCount;
  document.getElementById("stat-avgtime").textContent = sessionQueryCount > 0
    ? (sessionTotalTime / sessionQueryCount).toFixed(0) + "ms" : "—";
  document.getElementById("stat-avgconf").textContent = sessionQueryCount > 0
    ? ((sessionTotalConf / sessionQueryCount) * 100).toFixed(0) + "%" : "—";
}