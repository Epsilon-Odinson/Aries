// Tiny fetch wrapper for the Aries backend.
// In production the backend serves this app, so /api is relative.
// In dev, VITE_API_URL can point at http://localhost:8000.
// In the desktop shell (Electron) the page is a LOCAL file, so there
// is no relative /api — window.ariesDesktop (set by the shell's
// preload) gives the local Python service address and the cloud API
// address, and the app picks an engine:
//
// - "local" (desktop only, default when the local service is up):
//   the full backend runs on this PC — chat, memory, tasks, voice,
//   face login, everything — against a local database in ~/.aries.
//   The local service IS the backend; the cloud is not involved.
// - "cloud": the Render backend (aries-ahvo.onrender.com), the same
//   one the website uses. The fallback when the local service is
//   down, and the only engine on the web.
//
// The engine is stored per device in localStorage ("ariesEngine").
// Each engine has its own session token ("ariesToken" for the cloud,
// "ariesTokenLocal" for this PC) — the local and cloud accounts are
// separate accounts, so one token can never be sent to the other.
const DESK =
  (typeof window !== "undefined" && window.ariesDesktop) || null;
export const isDesktop = !!DESK;
const CLOUD_BASE = DESK ? DESK.cloudApi : import.meta.env.VITE_API_URL || "";
const ENGINE_KEY = "ariesEngine";

export function getEngine() {
  if (!DESK) return "cloud";
  try {
    const e = window.localStorage.getItem(ENGINE_KEY);
    if (e === "cloud" || e === "local") return e;
  } catch { /* ignore */ }
  return DESK.localApi ? "local" : "cloud";
}
export function setEngine(mode) {
  try {
    window.localStorage.setItem(ENGINE_KEY, mode === "cloud" ? "cloud" : "local");
  } catch { /* ignore */ }
}
// Short label for the UI: where this session's data lives.
export function engineLabel() {
  return getEngine() === "local" ? "on this PC" : "in the cloud";
}
function apiBase() {
  if (!DESK) return CLOUD_BASE;
  if (getEngine() === "local" && DESK.localApi) return DESK.localApi;
  return DESK.cloudApi;
}
export function getApiBase() {
  return apiBase();
}
// A fetch that failed before any HTTP status came back (service down,
// no network) — as opposed to a 401/403/503 from a live server.
export function isNetworkError(err) {
  return !!err && typeof err.status === "undefined";
}

// Accounts: the session token lives in localStorage and rides on
// every API call as Authorization: Bearer. The server rejects any
// data call without a live token (401) and scopes every row to the
// signed-in account — Epsilon's chats and memories are his, Kelfy's
// are hers. OAuth connect opens a new tab, which cannot send a
// header, so the token travels as ?token= on that one same-origin
// URL only — it is never forwarded to Google or Spotify.
function tokenKey() {
  return getEngine() === "local" && DESK ? "ariesTokenLocal" : "ariesToken";
}
export function getToken() {
  try { return window.localStorage.getItem(tokenKey()) || null; } catch { return null; }
}
export function setToken(token) {
  try {
    if (token) window.localStorage.setItem(tokenKey(), token);
    else window.localStorage.removeItem(tokenKey());
  } catch { /* ignore */ }
}
function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function call(path, opts = {}) {
  const res = await fetch(`${apiBase()}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const err = new Error(data.detail || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

// Local service (desktop shell only): PC control, microphone, wake
// phrase. These never run on the web — the functions throw a plain
// error there so a desktop-only call can never silently do nothing.
async function localCall(path, opts = {}) {
  if (!DESK || !DESK.localApi) {
    throw new Error("That only works in the desktop app.");
  }
  const res = await fetch(`${DESK.localApi}${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = { ok: false, error: text };
  }
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || data.why || `Local service failed (${res.status})`);
  }
  return data;
}

export const local = {
  // The sidecar may take a moment to start; health says if it is up.
  health: () => localCall("/health"),
  micAvailable: () => localCall("/mic/available"),
  // One dictation. Returns {text} or {heard: false}; throws a plain
  // error when the mic is unusable — the shell stays open either way.
  listen: (timeout = 12) =>
    localCall("/mic/listen", {
      method: "POST",
      body: JSON.stringify({ timeout }),
    }),
  wakeStart: () => localCall("/mic/wake/start", { method: "POST" }),
  wakeStop: () => localCall("/mic/wake/stop", { method: "POST" }),
  wakePoll: () => localCall("/mic/wake/poll"),
  // PC flow: local NVIDIA LLM plans the steps; the app confirms them,
  // then executes. Returns {is_pc, reply, steps, lines, destructive,
  // needs_key, needs_pc}.
  localChat: (message) =>
    localCall("/local/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    }),
  pcDescribe: (steps) =>
    localCall("/local/pc/describe", {
      method: "POST",
      body: JSON.stringify({ steps }),
    }),
  pcExecute: (steps) =>
    localCall("/local/pc/execute", {
      method: "POST",
      body: JSON.stringify({ steps }),
    }),
  pcEnabled: () => localCall("/local/pc/enabled"),
  setPcEnabled: (enabled) =>
    localCall("/local/pc/enabled", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  keyStatus: () => localCall("/local/key/status"),
  saveKey: (key) =>
    localCall("/local/key", {
      method: "POST",
      body: JSON.stringify({ key }),
    }),
  // ElevenLabs voice key for talk-back on this PC (optional — without
  // it talk-back uses the device's built-in voices).
  elevenKeyStatus: () => localCall("/local/elevenlabs/key/status"),
  saveElevenKey: (key) =>
    localCall("/local/elevenlabs/key", {
      method: "POST",
      body: JSON.stringify({ key }),
    }),
};

export const api = {
  health: () => call("/api/health"),
  // Accounts. register/login return {token, user}; the caller stores
  // the token with setToken(). me() 401s when the token is dead.
  register: (username, password) =>
    call("/api/auth/register", { method: "POST", body: JSON.stringify({ username, password }) }),
  login: (username, password) =>
    call("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => call("/api/auth/logout", { method: "POST", body: JSON.stringify({}) }),
  me: () => call("/api/auth/me"),
  // Admin only (the account holding the admin username).
  adminUsers: () => call("/api/admin/users"),
  adminDeleteUser: (username) =>
    call(`/api/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" }),
  state: () => call("/api/state"),
  // ElevenLabs voices on the server. 503 when the voice key is not set.
  voices: () => call("/api/voices"),
  // Talk-back audio as an MP3 blob. Throws on 503 (not configured) or
  // any ElevenLabs error — the caller falls back to browser voices.
  tts: async (text, voiceId) => {
    const res = await fetch(`${apiBase()}/api/tts`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ text, voice_id: voiceId || undefined }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const err = new Error(data.detail || `Voice failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return res.blob();
  },
  chat: (message, useSearch, consent) =>
    call("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, use_search: useSearch, consent }),
    }),
  clearChat: (consent) =>
    call("/api/chat/clear", { method: "POST", body: JSON.stringify({ consent }) }),
  saveProfile: (profile) =>
    call("/api/profile", { method: "POST", body: JSON.stringify(profile) }),
  savePersonality: (p) =>
    call("/api/personality", { method: "POST", body: JSON.stringify(p) }),
  addMemory: (text, category) =>
    call("/api/memories", { method: "POST", body: JSON.stringify({ text, category }) }),
  approveMemory: (id) => call(`/api/memories/${id}/approve`, { method: "POST" }),
  pinMemory: (id) => call(`/api/memories/${id}/pin`, { method: "POST" }),
  deleteMemory: (id) => call(`/api/memories/${id}`, { method: "DELETE" }),
  importMemories: (file) => {
    const form = new FormData();
    form.append("file", file);
    form.append("consent", "true");
    return api.upload("/api/memories/import", form);
  },
  addTask: (task) => call("/api/tasks", { method: "POST", body: JSON.stringify(task) }),
  toggleTask: (id) => call(`/api/tasks/${id}/done`, { method: "POST" }),
  deleteTask: (id) => call(`/api/tasks/${id}`, { method: "DELETE" }),
  markReminded: (id) => call(`/api/tasks/${id}/reminded`, { method: "POST" }),
  dueTasks: () => call("/api/tasks/due"),
  savePermission: (capability, setting) =>
    call("/api/permissions", {
      method: "POST",
      body: JSON.stringify({ capability, setting }),
    }),
  // Connections (Settings → Connections). Tokens never leave the server;
  // this only reports which services are connected and which account.
  connections: () => call("/api/connections"),
  // The connect page opens in a new tab (no Authorization header
  // possible), so the session token rides as ?token= on this
  // same-origin URL. The provider never sees it.
  connectUrl: (service) =>
    `${apiBase()}/api/connections/${service}/connect?token=${encodeURIComponent(getToken() || "")}`,
  disconnectConnection: (service) =>
    call(`/api/connections/${service}/disconnect`, { method: "POST" }),
  // Multipart endpoints use fetch directly (no JSON content-type).
  upload: async (path, form) => {
    const res = await fetch(`${apiBase()}${path}`, { method: "POST", headers: { ...authHeaders() }, body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.detail || `Upload failed (${res.status})`);
      err.status = res.status;
      throw err;
    }
    return data;
  },
  enrollFace: (file, name, pin) => {
    const form = new FormData();
    form.append("file", file);
    form.append("name", name || "");
    form.append("pin", pin || "");
    return api.upload("/api/face/enroll", form);
  },
  verifyFace: (file) => {
    const form = new FormData();
    form.append("file", file);
    return api.upload("/api/face/verify", form);
  },
  removeFace: () => call("/api/face/remove", { method: "POST" }),
  setPin: (pin) =>
    call("/api/face/set-pin", { method: "POST", body: JSON.stringify({ pin }) }),
  unlockPin: (pin) =>
    call("/api/face/unlock-pin", { method: "POST", body: JSON.stringify({ pin }) }),
  describeScreenshot: (file, question, consent) => {
    const form = new FormData();
    form.append("file", file);
    form.append("question", question || "");
    form.append("consent", consent ? "true" : "false");
    return api.upload("/api/screenshot", form);
  },
};
