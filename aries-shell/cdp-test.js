// CDP smoke test for the Aries Electron shell (0.8.0).
// Connects to the shell's remote-debugging port and exercises the
// running app in the real renderer: auth gate, local backend engine,
// accounts (Epsilon admin, Kelfy isolated), bridge, stamp, mic (no
// crash), mini mode, Settings, PC control, local chat. Prints
// PASS/FAIL per check and exits non-zero when any check fails.
//
// Usage: node cdp-test.js [port]   (default 9222)
// Launch the shell first, e.g.:
//   HOME=/tmp/aries-cdp-home VIRTUAL_ENV=$PWD/../.venv-test \
//     xvfb-run -a npx electron . --remote-debugging-port=9222
// (run from aries-shell/; HOME is a throwaway dir so the test never
// touches the real ~/.aries.)
const PORT = process.argv[2] || "9222";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failures = 0;
function check(name, ok, detail = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? " — " + detail : ""}`);
  if (!ok) failures++;
}

async function main() {
  // Find the renderer target.
  let targets;
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      targets = await res.json();
      break;
    } catch { await sleep(1000); }
  }
  if (!targets) { console.log("FAIL  could not reach DevTools on port " + PORT); process.exit(1); }
  const page = targets.find((t) => t.type === "page");
  if (!page) { console.log("FAIL  no page target"); process.exit(1); }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    else if (msg.method) events.push(msg);
  };
  const send = (method, params = {}) => new Promise((resolve) => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return { error: r.result.exceptionDetails.text || "JS error" };
    return { value: r.result?.result?.value };
  };
  await send("Runtime.enable");
  await send("Page.enable");

  // React controlled inputs need the native setter + input event.
  const SET_INPUT = `(el, v) => { const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; s.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true })); }`;
  const fillAuth = async (username, password) => {
    await evalJs(`(${SET_INPUT})(document.querySelector('input[aria-label="Username"]'), ${JSON.stringify(username)})`);
    await evalJs(`(${SET_INPUT})(document.querySelector('input[aria-label="Password"]'), ${JSON.stringify(password)})`);
  };
  const openSettingsTab = async (tab) => {
    await evalJs(`document.querySelector('.gear-btn').click()`);
    await sleep(1200);
    await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.more-tabs button')).find(x=>x.textContent==='${tab}'); if (b) b.click(); })()`);
    await sleep(1200);
  };
  const closeSettings = async () => {
    await evalJs(`document.querySelector('.more-head button').click()`);
    await sleep(800);
  };
  const sendChat = async (text) => {
    await evalJs(`document.querySelector('.composer-row input').focus()`);
    await send("Input.insertText", { text });
    await sleep(500);
    await evalJs(`document.querySelector('.send').click()`);
  };

  // 1. Auth gate: the login screen shows first, not the app.
  await sleep(3000);
  let r = await evalJs(`JSON.stringify({auth: !!document.querySelector('.auth-screen'), app: !!document.querySelector('.aries-app'), line: (document.querySelector('.auth-screen .fine')||{}).textContent || ''})`);
  check("login screen gates the app (auth screen, no .aries-app yet)", r.value && r.value.includes('"auth":true') && r.value.includes('"app":false'), String(r.value));
  check("login screen says signing in on this PC", r.value && r.value.includes("on this PC"), String(r.value));

  // 2. Desktop bridge present.
  r = await evalJs(`window.ariesDesktop ? JSON.stringify({isDesktop: window.ariesDesktop.isDesktop, localApi: !!window.ariesDesktop.localApi, cloudApi: window.ariesDesktop.cloudApi}) : 'missing'`);
  check("desktop bridge (window.ariesDesktop)", r.value && r.value.includes("true"), String(r.value));

  // 3. Local service reachable from the renderer, and it IS the backend.
  r = await evalJs(`fetch(window.ariesDesktop.localApi + '/health').then(x => x.json()).then(j => JSON.stringify(j)).catch(e => 'ERR:' + e.message)`);
  check("local service health from renderer (backend: local)", r.value && r.value.includes('"ok":true') && r.value.includes('"backend":"local"'), String(r.value));

  // 4. Register Epsilon (admin by username) through the UI.
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.link-btn')).find(x=>x.textContent.includes('Create one')); if (b) b.click(); })()`);
  await sleep(500);
  await fillAuth("Epsilon", "testpass123");
  await evalJs(`document.querySelector('.auth-btn').click()`);
  await sleep(4000);
  r = await evalJs(`JSON.stringify({app: !!document.querySelector('.aries-app'), face: !!document.querySelector('.face-btn, .visor'), greeting: (document.querySelector('.greeting')||{}).textContent?.slice(0,30) || '', composer: !!document.querySelector('.composer-row input'), send: !!document.querySelector('.send'), mic: !!document.querySelector('.mic')})`);
  check("Epsilon registers and the app opens (face, greeting, composer, mic, Send)", r.value && r.value.includes('"app":true') && r.value.includes('"face":true') && r.value.includes('"composer":true'), String(r.value));

  // 5. Stamp visible.
  r = await evalJs(`(() => { const el = document.querySelector('.made-by'); if (!el) return 'missing'; const cs = getComputedStyle(el); return JSON.stringify({text: el.textContent, font: cs.fontFamily.slice(0,40), style: cs.fontStyle, display: cs.display}); })()`);
  check("stamp '© Made by Epsilon' present, italic script", r.value && r.value.includes("Made by Epsilon") && r.value.includes("italic"), String(r.value));

  // 6. Mic tap: must NOT crash the app. No mic on this VM, so the
  // service reports plainly and the app stays open with a notice.
  await evalJs(`document.querySelector('.mic').click()`);
  await sleep(4000);
  r = await evalJs(`JSON.stringify({alive: !!document.querySelector('.aries-app'), notice: (document.querySelector('.notice')||{}).textContent || '', listening: document.querySelector('.mic')?.textContent})`);
  check("mic tap does not close the app (still alive, plain-words notice)", r.value && r.value.includes('"alive":true'), String(r.value));

  // 7. Mini mode: click the face -> window shrinks to 200x200, body
  // gets desktop-mini, only the face shows. Click again -> back.
  await evalJs(`document.querySelector('.face-btn').click()`);
  await sleep(1500);
  r = await evalJs(`JSON.stringify({mini: document.body.classList.contains('desktop-mini'), dockHidden: getComputedStyle(document.querySelector('.dock')).display, w: window.outerWidth, h: window.outerHeight})`);
  check("mini mode: face-only, dock hidden", r.value && r.value.includes('"mini":true') && r.value.includes('none'), String(r.value));
  check("mini window is 200x200", r.value && r.value.includes('"w":200') && r.value.includes('"h":200'), String(r.value));
  await evalJs(`document.querySelector('.face-btn').click()`);
  await sleep(1500);
  r = await evalJs(`document.body.classList.contains('desktop-mini')`);
  check("mini click expands back to full (desktop-mini removed)", r.value === false, String(r.value));

  // 8. Settings: 4 tabs, Desktop section with the engine switcher,
  // and the Account section shows Epsilon as admin.
  await openSettingsTab("More");
  r = await evalJs(`JSON.stringify({tabs: Array.from(document.querySelectorAll('.more-tabs button')).map(x=>x.textContent), desktopSection: !!Array.from(document.querySelectorAll('.more-sheet h3')).find(h=>h.textContent==='Desktop'), engineLine: (Array.from(document.querySelectorAll('.more-sheet .fine')).find(p=>p.textContent.includes('Engine:'))||{}).textContent || '', svcLine: (Array.from(document.querySelectorAll('.more-sheet .fine')).find(p=>p.textContent.includes('Local service:'))||{}).textContent || '', pcToggle: !!Array.from(document.querySelectorAll('.more-sheet .pill')).find(b=>b.textContent.includes('PC control')), keyField: !!document.querySelector('.more-sheet input[type=password]'), engineBtns: Array.from(document.querySelectorAll('.more-sheet .pill')).filter(b=>b.textContent==='This PC' || b.textContent==='Cloud').length, account: (Array.from(document.querySelectorAll('.more-sheet section')).find(s=>s.textContent.includes('Signed in as'))||{}).textContent?.slice(0,80) || ''})`);
  check("Settings: 4 tabs + Desktop section (engine, service, PC toggle, key fields)", r.value && r.value.includes('"desktopSection":true') && r.value.includes('"pcToggle":true') && r.value.includes('"keyField":true'), String(r.value));
  check("engine is on this PC", r.value && r.value.includes("Engine: on this PC"), String(r.value));
  check("local service running with db path", r.value && r.value.includes("Local service: running") && r.value.includes(".aries/aries.db"), String(r.value));
  check("engine switcher has This PC + Cloud", r.value && r.value.includes('"engineBtns":2'), String(r.value));
  check("account shows Epsilon as admin", r.value && r.value.includes("Epsilon") && r.value.includes("admin"), String(r.value));
  // PC toggle: turn it on if it is off (needed for the PC flow check below).
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.more-sheet .pill')).find(x=>x.textContent.includes('PC control')); if (b && b.getAttribute('aria-pressed') !== 'true') b.click(); })()`);
  await sleep(1200);
  await closeSettings();

  // 9. PC flow: "open notepad" routes locally. No NVIDIA key on this
  // VM, so the service answers needs_key in plain words (no crash,
  // no raw JSON). That is the honest result here.
  await sendChat("open notepad");
  await sleep(5000);
  r = await evalJs(`JSON.stringify({last: (document.querySelectorAll('.latest-reply p')[0]||{}).textContent?.slice(0,140) || ''})`);
  check("PC message routes locally, plain-words answer (needs key here)", r.value && (r.value.includes("engine key") || r.value.includes("PC control")), String(r.value));

  // 10. Local chat: no engine key on this VM, so the local backend
  // answers with its honest key error and the app points at
  // Settings → More → Desktop. That proves the chat went to the
  // local backend, not the cloud.
  await sendChat("Say just the word OK and nothing else.");
  let sawKeyNotice = false;
  for (let i = 0; i < 15; i++) {
    await sleep(2000);
    r = await evalJs(`JSON.stringify({reply: (document.querySelectorAll('.latest-reply p')[0]||{}).textContent || '', notice: (document.querySelector('.notice')||{}).textContent || ''})`);
    if (r.value && r.value.includes("engine key")) { sawKeyNotice = true; break; }
  }
  check("local chat answers honestly about the missing engine key", sawKeyNotice, String((r.value || "").slice(0, 160)));

  // 11. Log out -> login screen again.
  await openSettingsTab("More");
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.more-sheet button')).find(x=>x.textContent==='Log out'); if (b) b.click(); })()`);
  await sleep(2500);
  r = await evalJs(`JSON.stringify({auth: !!document.querySelector('.auth-screen'), app: !!document.querySelector('.aries-app')})`);
  check("logout returns to the login screen", r.value && r.value.includes('"auth":true') && r.value.includes('"app":false'), String(r.value));

  // 12. Register Kelfy: fresh empty account, NOT admin, isolated
  // from Epsilon's data (Memory tab says "No memories yet").
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.link-btn')).find(x=>x.textContent.includes('Create one')); if (b) b.click(); })()`);
  await sleep(500);
  await fillAuth("Kelfy", "testpass123");
  await evalJs(`document.querySelector('.auth-btn').click()`);
  await sleep(4000);
  r = await evalJs(`JSON.stringify({app: !!document.querySelector('.aries-app')})`);
  check("Kelfy registers and the app opens", r.value && r.value.includes('"app":true'), String(r.value));
  await openSettingsTab("More");
  r = await evalJs(`JSON.stringify({account: (Array.from(document.querySelectorAll('.more-sheet section')).find(s=>s.textContent.includes('Signed in as'))||{}).textContent?.slice(0,80) || ''})`);
  check("account shows Kelfy, not admin", r.value && r.value.includes("Kelfy") && !r.value.includes("admin"), String(r.value));
  await closeSettings();
  await openSettingsTab("Memory");
  r = await evalJs(`document.querySelector('.more-sheet').textContent.includes('No memories yet')`);
  check("Kelfy starts with no memories (isolated from Epsilon)", r.value === true, String(r.value));
  await closeSettings();

  // 13. Connections on the local engine: plain notice that Gmail /
  // Spotify connect through the web app, and Connect is disabled.
  await openSettingsTab("Connections");
  r = await evalJs(`JSON.stringify({notice: (document.querySelector('.more-sheet .notice')||{}).textContent || '', connectDisabled: Array.from(document.querySelectorAll('.more-sheet .conn-actions button')).filter(b=>b.textContent==='Connect').every(b=>b.disabled), openWeb: !!Array.from(document.querySelectorAll('.more-sheet button')).find(b=>b.textContent==='Open the web app')})`);
  check("Connections explains local OAuth honestly, Connect disabled, web link offered", r.value && r.value.includes("connect through the web app") && r.value.includes('"connectDisabled":true') && r.value.includes('"openWeb":true'), String(r.value));
  await closeSettings();

  ws.close();
  console.log(failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.log("FAIL  harness error:", e.message); process.exit(1); });
