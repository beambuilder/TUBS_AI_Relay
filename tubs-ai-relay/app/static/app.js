"use strict";

const STORAGE_CHATS = "tubs-ai-relay.chats.v1";
const STORAGE_ACTIVE = "tubs-ai-relay.activeChat.v1";
const STORAGE_MODEL = "tubs-ai-relay.model.v1";

const $ = (id) => document.getElementById(id);

const state = {
  chats: [],
  activeId: null,
  inFlight: null, // AbortController of an in-progress completion
};

// ---------- persistence ----------
function loadChats() {
  try {
    const raw = localStorage.getItem(STORAGE_CHATS);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function saveChats() {
  localStorage.setItem(STORAGE_CHATS, JSON.stringify(state.chats));
}

function uid() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

// ---------- chat operations ----------
function getActive() {
  return state.chats.find((c) => c.id === state.activeId) || null;
}

function setActive(id) {
  state.activeId = id;
  if (id) localStorage.setItem(STORAGE_ACTIVE, id);
  else localStorage.removeItem(STORAGE_ACTIVE);
}

function newChat() {
  const chat = {
    id: uid(),
    title: "New chat",
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
    model: $("model-select").value || null,
  };
  state.chats.unshift(chat);
  setActive(chat.id);
  saveChats();
  renderSidebar();
  renderActive();
  $("input").focus();
}

function deleteActive() {
  const c = getActive();
  if (!c) return;
  if (!confirm(`Delete chat "${c.title}"? This cannot be undone.`)) return;
  state.chats = state.chats.filter((x) => x.id !== c.id);
  setActive(state.chats[0]?.id || null);
  saveChats();
  renderSidebar();
  renderActive();
}

function renameActive() {
  const c = getActive();
  if (!c) return;
  const t = prompt("Rename chat", c.title);
  if (t === null) return;
  const trimmed = t.trim();
  if (!trimmed) return;
  c.title = trimmed.slice(0, 80);
  c.updatedAt = Date.now();
  saveChats();
  renderSidebar();
  renderActive();
}

// ---------- rendering ----------
function renderSidebar() {
  const ul = $("chat-list");
  ul.innerHTML = "";
  if (state.chats.length === 0) {
    const li = document.createElement("li");
    li.style.cursor = "default";
    li.style.color = "var(--text-dim)";
    li.textContent = "No chats yet";
    ul.appendChild(li);
    return;
  }
  for (const c of state.chats) {
    const li = document.createElement("li");
    li.className = c.id === state.activeId ? "active" : "";
    li.title = c.title;
    li.textContent = c.title || "Untitled";
    const ts = document.createElement("span");
    ts.className = "ts";
    const when = new Date(c.updatedAt || c.createdAt || Date.now());
    ts.textContent = relativeTime(when);
    li.appendChild(ts);
    li.addEventListener("click", () => {
      setActive(c.id);
      renderSidebar();
      renderActive();
    });
    ul.appendChild(li);
  }
}

function renderActive() {
  const c = getActive();
  const titleEl = $("chat-title");
  const subEl = $("chat-sub");
  const msgsEl = $("messages");
  msgsEl.innerHTML = "";
  if (!c) {
    titleEl.textContent = "No chat selected";
    subEl.textContent = "";
    renderEmptyHelp();
    return;
  }
  titleEl.textContent = c.title || "Untitled";
  subEl.textContent = `${c.messages.length} message${c.messages.length === 1 ? "" : "s"}`;
  if (c.messages.length === 0) {
    renderEmptyHelp();
    return;
  }
  for (const m of c.messages) addMessage(m.role, m.content);
  scrollToBottom();
}

function renderEmptyHelp() {
  const wrap = document.createElement("div");
  wrap.className = "empty";
  wrap.innerHTML = `
    <h2>Start a conversation</h2>
    <p>Pick a model from the sidebar and type a message below.</p>
    <p>Other Umbrel apps can reach this relay's OpenAI-compatible API at:</p>
    <p><code>http://tubs-ai-relay_server_1:8000/v1</code></p>
  `;
  $("messages").appendChild(wrap);
}

function addMessage(role, content) {
  const tpl = $("message-template").content.cloneNode(true);
  const wrapper = tpl.querySelector(".msg");
  wrapper.classList.add(role);
  const bubble = wrapper.querySelector(".bubble");
  bubble.textContent = content;
  $("messages").appendChild(wrapper);
  return { wrapper, bubble };
}

function scrollToBottom() {
  const el = $("messages");
  el.scrollTop = el.scrollHeight;
}

function relativeTime(d) {
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

// ---------- send ----------
async function sendMessage(text) {
  if (state.inFlight) return; // already streaming
  let chat = getActive();
  if (!chat) {
    newChat();
    chat = getActive();
  }
  const model = $("model-select").value || chat.model || null;
  if (!model) {
    setStatus("Pick a model first.", "error");
    return;
  }
  chat.model = model;

  // Remove empty-help if present
  const helpEl = $("messages").querySelector(".empty");
  if (helpEl) helpEl.remove();

  chat.messages.push({ role: "user", content: text });
  if (chat.messages.length === 1) {
    chat.title = text.slice(0, 60);
  }
  chat.updatedAt = Date.now();
  saveChats();
  renderSidebar();
  addMessage("user", text);
  scrollToBottom();

  const { bubble } = addMessage("assistant", "");
  bubble.classList.add("thinking");

  const ctrl = new AbortController();
  state.inFlight = ctrl;
  setSending(true);
  let accumulated = "";

  try {
    const res = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        messages: chat.messages.map((m) => ({ role: m.role, content: m.content })),
        stream: true,
      }),
      signal: ctrl.signal,
    });

    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`HTTP ${res.status}: ${errText}`);
    }
    if (!res.body) {
      throw new Error("Streaming not supported by the browser.");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    outer: while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const eventBlock = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of eventBlock.split("\n")) {
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trimStart();
          if (payload === "[DONE]") break outer;
          let evt;
          try {
            evt = JSON.parse(payload);
          } catch {
            continue;
          }
          if (evt.error) {
            throw new Error(evt.error.message || "Upstream error");
          }
          const delta = evt.choices?.[0]?.delta;
          if (delta?.content) {
            if (bubble.classList.contains("thinking")) {
              bubble.classList.remove("thinking");
            }
            accumulated += delta.content;
            bubble.textContent = accumulated;
            scrollToBottom();
          }
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      bubble.classList.remove("thinking");
      if (!accumulated) bubble.textContent = "(stopped)";
    } else {
      const wrapper = bubble.closest(".msg");
      wrapper.classList.remove("assistant");
      wrapper.classList.add("error");
      bubble.classList.remove("thinking");
      bubble.textContent = `Error: ${err.message}`;
      setStatus(err.message, "error");
    }
  } finally {
    state.inFlight = null;
    setSending(false);
    bubble.classList.remove("thinking");
  }

  if (accumulated) {
    chat.messages.push({ role: "assistant", content: accumulated });
    chat.updatedAt = Date.now();
    saveChats();
    renderSidebar();
    renderActive(); // re-render so message count updates
  }
}

function setSending(sending) {
  $("send-btn").disabled = sending;
  $("send-btn").textContent = sending ? "Stop" : "Send";
}

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text || "";
  el.className = "status" + (kind ? " " + kind : "");
}

// ---------- models ----------
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const cfg = await res.json();
    const sel = $("model-select");
    sel.innerHTML = "";
    const optGroupCloud = document.createElement("optgroup");
    optGroupCloud.label = "Cloud";
    for (const m of cfg.cloud_models || []) {
      const o = document.createElement("option");
      o.value = m;
      o.textContent = m;
      optGroupCloud.appendChild(o);
    }
    const optGroupLocal = document.createElement("optgroup");
    optGroupLocal.label = "On-premise";
    for (const m of cfg.local_models || []) {
      const o = document.createElement("option");
      o.value = m;
      o.textContent = m;
      optGroupLocal.appendChild(o);
    }
    sel.appendChild(optGroupCloud);
    sel.appendChild(optGroupLocal);
    const saved = localStorage.getItem(STORAGE_MODEL) || cfg.default_model;
    if (saved && [...sel.options].some((o) => o.value === saved)) {
      sel.value = saved;
    }
    if (!cfg.tubs_api_key_configured) {
      setStatus("TUBS_API_KEY is not configured on the relay.", "error");
    } else {
      setStatus(`v${cfg.version || "?"} • key OK`, "ok");
    }
  } catch (e) {
    setStatus(`Could not load config: ${e.message}`, "error");
  }
}

// ---------- wiring ----------
function init() {
  state.chats = loadChats();
  state.activeId = localStorage.getItem(STORAGE_ACTIVE);
  if (state.activeId && !getActive()) {
    state.activeId = state.chats[0]?.id || null;
  }

  $("new-chat-btn").addEventListener("click", newChat);
  $("rename-btn").addEventListener("click", renameActive);
  $("delete-btn").addEventListener("click", deleteActive);

  $("model-select").addEventListener("change", () => {
    const m = $("model-select").value;
    localStorage.setItem(STORAGE_MODEL, m);
    const c = getActive();
    if (c) {
      c.model = m;
      saveChats();
    }
  });

  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.inFlight) {
      state.inFlight.abort();
      return;
    }
    const text = $("input").value.trim();
    if (!text) return;
    $("input").value = "";
    autoSize($("input"));
    sendMessage(text);
  });

  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("composer").requestSubmit();
    }
  });
  $("input").addEventListener("input", () => autoSize($("input")));

  renderSidebar();
  renderActive();
  loadConfig();
  $("input").focus();
}

function autoSize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 200) + "px";
}

document.addEventListener("DOMContentLoaded", init);
