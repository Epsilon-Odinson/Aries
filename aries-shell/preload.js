// Aries desktop shell — preload. Runs before the React app loads and
// exposes a small, safe bridge: the local sidecar address, the cloud
// API address, and the mini-mode / window controls. No Node APIs leak
// into the page; the React app only ever sees this object.
const { contextBridge, ipcRenderer } = require("electron");

function localPort() {
  const arg = (process.argv || []).find((a) => a.startsWith("--aries-local-port="));
  if (!arg) return 0;
  const n = parseInt(arg.split("=")[1], 10);
  return Number.isFinite(n) ? n : 0;
}

const port = localPort();

contextBridge.exposeInMainWorld("ariesDesktop", {
  isDesktop: true,
  // Local Python service (PC control, mic, wake). 0 when it failed to start.
  localApi: port ? `http://127.0.0.1:${port}` : "",
  // Render backend (chat, search, memory, voice, face). The shell never
  // loads this as a page — the React app only calls its /api endpoints.
  cloudApi: "https://aries-ahvo.onrender.com",
  // Mini mode: resize the window to the face-only mini window / back.
  setMini: (mini) => ipcRenderer.invoke("set-mini", !!mini),
  getServicePort: () => ipcRenderer.invoke("get-service-port"),
  quit: () => ipcRenderer.invoke("quit"),
});
