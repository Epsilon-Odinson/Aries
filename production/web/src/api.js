// Tiny fetch wrapper for the Aries backend.
// In production the backend serves this app, so /api is relative.
// In dev, VITE_API_URL can point at http://localhost:8000.
const BASE = import.meta.env.VITE_API_URL || "";

async function call(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
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

export const api = {
  health: () => call("/api/health"),
  state: () => call("/api/state"),
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
  // Multipart endpoints use fetch directly (no JSON content-type).
  upload: async (path, form) => {
    const res = await fetch(`${BASE}${path}`, { method: "POST", body: form });
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
