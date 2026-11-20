// Aries desktop shell 0.7.0 — Electron main process.
//
// The shell renders the SAME React code as the web, bundled locally
// (aries-shell/app/, built from production/web). It NEVER loads the
// live website — there is no URL anywhere here. It is a real
// installed PC program that happens to render the same React code
// locally, so it is pixel-identical to the web AND fully a desktop
// app (PC control, microphone, wake phrase via the local Python
// sidecar on 127.0.0.1).
//
// An unhandled error shows a plain-words message and never kills
// the app silently. The sidecar is stopped when the app quits.

const { app, BrowserWindow, ipcMain, shell, dialog, screen } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const http = require("http");

const CLOUD_API = "https://aries-ahvo.onrender.com";
const MINI_SIZE = 200; // face-only mini window
const MINI_GAP_BOTTOM = 56; // clear gap above the taskbar
const MINI_GAP_RIGHT = 24;
const SIDECAR_WAIT_MS = 30000;

let mainWindow = null;
let sidecar = null;
let sidecarPort = 0;
let savedBounds = null;
let isMini = false;

function log(...args) {
  console.log("[aries-shell]", ...args);
}

// --- global error handling: never die silently -------------------------
process.on("uncaughtException", (err) => {
  console.error("[aries-shell] uncaught:", err);
  try {
    dialog.showMessageBoxSync({
      type: "warning",
      title: "Aries",
      message: "Something unexpected happened, but Aries is still here.",
      detail: String((err && err.message) || err),
    });
  } catch { /* ignore */ }
});
process.on("unhandledRejection", (reason) => {
  console.error("[aries-shell] unhandled rejection:", reason);
});

// --- sidecar ------------------------------------------------------------
// Dev: `python -m aries.service --port 0` (cwd = desktop staging dir).
// Packaged: resources/aries-service/aries-service.exe --port 0.
// Override with ARIES_SERVICE_CMD (a full command line, split on space).
function sidecarCommand() {
  if (process.env.ARIES_SERVICE_CMD) {
    const parts = process.env.ARIES_SERVICE_CMD.split(" ").filter(Boolean);
    return {
      cmd: parts[0],
      args: parts.slice(1),
      // The service is `python -m aries.service`, so it must run
      // from the desktop staging dir (the `aries` package root).
      cwd: process.env.ARIES_SERVICE_CWD || path.resolve(__dirname, ".."),
    };
  }
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, "aries-service", "aries-service.exe");
    return { cmd: exe, args: ["--port", "0"], cwd: process.resourcesPath };
  }
  const staging = path.resolve(__dirname, "..");
  const venv = process.env.VIRTUAL_ENV;
  let python = process.platform === "win32" ? "python" : "python3";
  if (venv) {
    python = path.join(venv, process.platform === "win32" ? "Scripts" : "bin",
      process.platform === "win32" ? "python.exe" : "python");
  }
  return { cmd: python, args: ["-m", "aries.service", "--port", "0"], cwd: staging };
}

function waitForHealth(port, timeoutMs) {
  const started = Date.now();
  return new Promise((resolve) => {
    const tick = () => {
      const req = http.get(
        { host: "127.0.0.1", port, path: "/health", timeout: 2000 },
        (res) => {
          let body = "";
          res.on("data", (c) => { body += c; });
          res.on("end", () => {
            try {
              const data = JSON.parse(body);
              if (data.ok) return resolve(true);
            } catch { /* not ready */ }
            retry();
          });
        }
      );
      req.on("error", retry);
      req.on("timeout", () => { req.destroy(); retry(); });
      function retry() {
        if (Date.now() - started > timeoutMs) return resolve(false);
        setTimeout(tick, 300);
      }
    };
    tick();
  });
}

function startSidecar() {
  return new Promise((resolve) => {
    const { cmd, args, cwd } = sidecarCommand();
    log("starting sidecar:", cmd, args.join(" "), "in", cwd);
    let child;
    try {
      // windowsHide: the sidecar is a console build (it must print its
      // port on stdout), but no console window may ever show.
      child = spawn(cmd, args, { cwd, stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    } catch (err) {
      console.error("[aries-shell] could not spawn sidecar:", err);
      return resolve(0);
    }
    sidecar = child;
    let port = 0;
    let settled = false;
    const finish = (p) => {
      if (settled) return;
      settled = true;
      sidecarPort = p;
      resolve(p);
    };
    const onLine = (line) => {
      const m = /ARIES_SERVICE_PORT=(\d+)/.exec(line);
      if (m) {
        port = parseInt(m[1], 10);
        waitForHealth(port, SIDECAR_WAIT_MS).then((ok) => {
          log("sidecar health:", ok ? "ok" : "TIMEOUT", "port", port);
          finish(ok ? port : 0);
        });
      }
    };
    let buf = "";
    child.stdout.on("data", (d) => {
      buf += d.toString();
      let idx;
      while ((idx = buf.indexOf("\n")) >= 0) {
        onLine(buf.slice(0, idx));
        buf = buf.slice(idx + 1);
      }
    });
    child.stderr.on("data", (d) => process.stderr.write(`[sidecar] ${d}`));
    child.on("exit", (code) => {
      log("sidecar exited with code", code);
      if (!settled) finish(0);
    });
    setTimeout(() => finish(port || 0), SIDECAR_WAIT_MS + 5000);
  });
}

function stopSidecar() {
  if (sidecar && !sidecar.killed) {
    try { sidecar.kill(); } catch { /* ignore */ }
    sidecar = null;
  }
}

// --- windows ------------------------------------------------------------
function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    fullscreen: true,
    frame: false,
    transparent: true,
    backgroundColor: "#0d0010",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      additionalArguments: [`--aries-local-port=${sidecarPort}`],
    },
  });
  mainWindow = win;
  win.once("ready-to-show", () => {
    win.show();
    if (process.env.ARIES_DEVTOOLS === "1") win.webContents.openDevTools({ mode: "detach" });
  });
  win.on("closed", () => { mainWindow = null; });
  win.webContents.setWindowOpenHandler(({ url }) => {
    // OAuth and external links open in the system browser, never in the app.
    shell.openExternal(url).catch(() => {});
    return { action: "deny" };
  });
  win.webContents.on("did-fail-load", (_e, code, desc) => {
    console.error("[aries-shell] failed to load:", code, desc);
  });
  const entry = path.join(__dirname, "app", "index.html");
  if (!fs.existsSync(entry)) {
    dialog.showErrorBox(
      "Aries",
      "The bundled app is missing (app/index.html). Run `npm run build:web` first."
    );
    app.quit();
    return;
  }
  win.loadFile(entry);
}

function miniBounds() {
  const area = screen.getPrimaryDisplay().workArea; // excludes the taskbar
  return {
    x: area.x + area.width - MINI_SIZE - MINI_GAP_RIGHT,
    y: area.y + area.height - MINI_SIZE - MINI_GAP_BOTTOM,
    width: MINI_SIZE,
    height: MINI_SIZE,
  };
}

function setMini(mini) {
  const win = mainWindow;
  if (!win) return;
  if (mini === isMini) return;
  isMini = mini;
  if (mini) {
    savedBounds = win.getBounds();
    win.setFullScreen(false);
    win.setBounds(miniBounds());
    win.setAlwaysOnTop(true, "screen-saver");
    win.setResizable(false);
  } else {
    win.setAlwaysOnTop(false);
    win.setResizable(true);
    win.setFullScreen(true);
    if (savedBounds) win.setBounds(savedBounds);
  }
}

// --- app lifecycle ------------------------------------------------------
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.whenReady().then(async () => {
    const { Menu } = require("electron");
    Menu.setApplicationMenu(null);
    await startSidecar();
    createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
  app.on("window-all-closed", () => {
    stopSidecar();
    if (process.platform !== "darwin") app.quit();
  });
  app.on("before-quit", stopSidecar);
}

ipcMain.handle("set-mini", (_e, mini) => {
  setMini(!!mini);
  return isMini;
});
ipcMain.handle("get-service-port", () => sidecarPort);
ipcMain.handle("quit", () => app.quit());
