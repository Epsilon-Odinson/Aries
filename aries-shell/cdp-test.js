// CDP smoke test for the Aries Electron shell (0.9.0).
// Connects to the shell's remote-debugging port and exercises the
// running app in the real renderer: auth gate, engine default
// (cloud, no keys to paste), login-screen engine choice, accounts
// (Epsilon admin on This PC, Kelfy isolated on the cloud), bridge,
// stamp, mic (no crash), mini mode, Settings, PC control, local
// chat, and the key-missing -> cloud switch. Prints PASS/FAIL per
// check and exits non-zero when any check fails.
//
// TEST FIXTURE — fake cloud: the test never touches the live cloud
// (no test accounts on the real service). window.ariesDesktop is
// frozen by the preload bridge, so the cloud URL cannot be pointed
// at the local service by assignment — instead the test arms CDP
// Fetch interception for https://aries-ahvo.onrender.com/* and
// rewrites every such request to the local service (continueRequest
// with an overridden URL). The engine logic under test (default,
// pills, per-engine tokens, switch) is the real code; only the
// cloud URL is local, and the local backend answers with
// CORS_ORIGINS=* so the file:// page reads the responses. A live
// model reply on the real cloud is out of reach here (this VM has
// no NVIDIA key) and is covered by the live health checks instead:
// on the real cloud the key is configured, so chat there answers
// instead of returning the honest key error the local backend gives
// here.
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
  // The window (and its page target) can take a few seconds to
  // appear after launch — retry until it shows up.
  let page = null;
  for (let i = 0; i < 30 && !page; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      page = targets.find((t) => t.type === "page");
    } catch { /* ignore, retry */ }
    if (!page) await sleep(1000);
  }
  if (!page) { console.log("FAIL  no page target"); process.exit(1); }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  const events = [];
  const send = (method, params = {}) => new Promise((resolve) => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  // Fake cloud (see header): every request the page makes to the
  // real cloud URL is rewritten to the local service before it
  // leaves. localApi is filled in right after connecting.
  let localApi = "";
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    else if (msg.method === "Fetch.requestPaused") {
      try {
        const u = new URL(msg.params.request.url);
        const dest = localApi + u.pathname + u.search;
        send("Fetch.continueRequest", { requestId: msg.params.requestId, url: dest });
      } catch { send("Fetch.continueRequest", { requestId: msg.params.requestId }); }
    }
    else if (msg.method) events.push(msg);
  };
  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.result?.exceptionDetails) return { error: r.result.exceptionDetails.text || "JS error" };
    return { value: r.result?.result?.value };
  };
  await send("Runtime.enable");
  await send("Page.enable");
  // Arm the fake cloud (see header): read the local service address
  // from the bridge, then rewrite every request to the real cloud
  // URL to the local service instead.
  const bridge = await evalJs(`window.ariesDesktop ? window.ariesDesktop.localApi : ''`);
  localApi = bridge.value || "";
  await send("Fetch.enable", { patterns: [{ urlPattern: "https://aries-ahvo.onrender.com/*", requestStage: "Request" }] });

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
  const authScreenState = async () => {
    const r = await evalJs(`JSON.stringify({auth: !!document.querySelector('.auth-screen'), app: !!document.querySelector('.aries-app'), line: (document.querySelector('.auth-screen .fine')||{}).textContent || '', pills: Array.from(document.querySelectorAll('.auth-screen .toggle-row .pill')).map(b=>({t: b.textContent, pressed: b.getAttribute('aria-pressed')})), stored: window.localStorage.getItem('ariesEngine')})`);
    return r.value || "";
  };

  // 1. Auth gate: the login screen shows first, not the app.
  await sleep(3000);
  let r = await evalJs(`JSON.stringify({auth: !!document.querySelector('.auth-screen'), app: !!document.querySelector('.aries-app')})`);
  check("login screen gates the app (auth screen, no .aries-app yet)", r.value && r.value.includes('"auth":true') && r.value.includes('"app":false'), String(r.value));

  // 2. Default engine is cloud (0.9.0: no stored preference, local
  // service up, but the default is still cloud — nobody has to paste
  // a key to get started). The login screen shows the choice with
  // the Cloud pill selected.
  let s = await authScreenState();
  check("default engine is cloud on a fresh profile (no stored preference)", s.includes('"stored":null') && s.includes("in the cloud"), s);
  check("login screen shows the engine choice, Cloud selected", s.includes("recommended, no setup") && s.includes("works offline, needs your own keys") && s.includes('"pressed":"true"'), s);

  // 3. Desktop bridge present, and the fake cloud is armed: a
  // renderer fetch to the cloud URL must come back from the LOCAL
  // service (0.9.0, backend: local) — not the real cloud.
  r = await evalJs(`window.ariesDesktop ? JSON.stringify({isDesktop: window.ariesDesktop.isDesktop, localApi: !!window.ariesDesktop.localApi, cloudApi: window.ariesDesktop.cloudApi}) : 'missing'`);
  check("desktop bridge (window.ariesDesktop)", r.value && r.value.includes("true"), String(r.value));
  r = await evalJs(`fetch(window.ariesDesktop.cloudApi + '/health').then(x => x.json()).then(j => JSON.stringify(j)).catch(e => 'ERR:' + e.message)`);
  check("cloud requests are intercepted to the local service (renderer fetch to the cloud URL returns local 0.9.0 health)", r.value && r.value.includes('"version":"0.9.0"') && r.value.includes('"backend":"local"'), String(r.value));

  // 4. Local service reachable from the renderer, and it IS the backend.
  r = await evalJs(`fetch(window.ariesDesktop.localApi + '/health').then(x => x.json()).then(j => JSON.stringify(j)).catch(e => 'ERR:' + e.message)`);
  check("local service health from renderer (0.9.0, backend: local, sqlite, db path)", r.value && r.value.includes('"ok":true') && r.value.includes('"backend":"local"') && r.value.includes('"version":"0.9.0"') && r.value.includes('"db":"sqlite"') && r.value.includes(".aries/aries.db"), String(r.value));

  // 5. Login-screen engine choice works: tap "On this PC" and the
  // line, the pill states and the stored preference all follow.
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.auth-screen .toggle-row .pill')).find(x=>x.textContent.includes('works offline')); if (b) b.click(); })()`);
  await sleep(800);
  s = await authScreenState();
  check("login-screen pill switches to This PC (line, pill, stored preference)", s.includes("on this PC") && s.includes('"stored":"local"'), s);

  // 6. Register Epsilon (admin by username) on This PC through the UI.
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.link-btn')).find(x=>x.textContent.includes('Create one')); if (b) b.click(); })()`);
  await sleep(500);
  await fillAuth("Epsilon", "testpass123");
  await evalJs(`document.querySelector('.auth-btn').click()`);
  await sleep(4000);
  r = await evalJs(`JSON.stringify({app: !!document.querySelector('.aries-app'), face: !!document.querySelector('.face-btn, .visor'), greeting: (document.querySelector('.greeting')||{}).textContent?.slice(0,30) || '', composer: !!document.querySelector('.composer-row input'), send: !!document.querySelector('.send'), mic: !!document.querySelector('.mic')})`);
  check("Epsilon registers on This PC and the app opens (face, greeting, composer, mic, Send)", r.value && r.value.includes('"app":true') && r.value.includes('"face":true') && r.value.includes('"composer":true'), String(r.value));

  // 7. Stamp visible.
  r = await evalJs(`(() => { const el = document.querySelector('.made-by'); if (!el) return 'missing'; const cs = getComputedStyle(el); return JSON.stringify({text: el.textContent, font: cs.fontFamily.slice(0,40), style: cs.fontStyle, display: cs.display}); })()`);
  check("stamp '© Made by Epsilon' present, italic script", r.value && r.value.includes("Made by Epsilon") && r.value.includes("italic"), String(r.value));

  // 8. Mic tap: must NOT crash the app. No mic on this VM, so the
  // service reports plainly and the app stays open with a notice.
  await evalJs(`document.querySelector('.mic').click()`);
  await sleep(4000);
  r = await evalJs(`JSON.stringify({alive: !!document.querySelector('.aries-app'), notice: (document.querySelector('.notice')||{}).textContent || '', listening: document.querySelector('.mic')?.textContent})`);
  check("mic tap does not close the app (still alive, plain-words notice)", r.value && r.value.includes('"alive":true'), String(r.value));

  // 9. Mini mode: click the face -> window shrinks to 200x200, body
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

  // 10. Settings: 5 tabs (Study joined in 0.9.0), Desktop section
  // with the engine switcher, and the Account section shows
  // Epsilon. The admin-only "Accounts" section is the admin proof
  // now — the account line itself no longer says "admin"
  // (professionalism sweep, 0.9.0 web). The service line no longer
  // carries the db path (same sweep); the path is asserted on the
  // /health JSON in check 4 instead.
  await openSettingsTab("More");
  r = await evalJs(`JSON.stringify({tabs: Array.from(document.querySelectorAll('.more-tabs button')).map(x=>x.textContent), desktopSection: !!Array.from(document.querySelectorAll('.more-sheet h3')).find(h=>h.textContent==='Desktop'), engineLine: (Array.from(document.querySelectorAll('.more-sheet .fine')).find(p=>p.textContent.includes('Engine:'))||{}).textContent || '', svcLine: (Array.from(document.querySelectorAll('.more-sheet .fine')).find(p=>p.textContent.includes('Local service:'))||{}).textContent || '', pcToggle: !!Array.from(document.querySelectorAll('.more-sheet .pill')).find(b=>b.textContent.includes('PC control')), keyField: !!document.querySelector('.more-sheet input[type=password]'), voiceKey: !!Array.from(document.querySelectorAll('.more-sheet label')).find(l=>l.textContent.includes('Voice key')), engineBtns: Array.from(document.querySelectorAll('.more-sheet .pill')).filter(b=>b.textContent==='This PC' || b.textContent==='Cloud').length, account: (Array.from(document.querySelectorAll('.more-sheet section')).find(s=>s.textContent.includes('Signed in as'))||{}).textContent?.slice(0,80) || '', adminSection: !!Array.from(document.querySelectorAll('.more-sheet h3')).find(h=>h.textContent==='Accounts')})`);
  check("Settings: 5 tabs incl. Study + Desktop section (engine, service, PC toggle, key fields)", r.value && r.value.includes('"Study"') && r.value.includes('"desktopSection":true') && r.value.includes('"pcToggle":true') && r.value.includes('"keyField":true'), String(r.value));
  check("Desktop has both the engine key and the ElevenLabs voice key fields", r.value && r.value.includes('"keyField":true') && r.value.includes('"voiceKey":true'), String(r.value));
  check("engine is on this PC", r.value && r.value.includes("Engine: on this PC"), String(r.value));
  check("local service running (plain words, no db path in the UI)", r.value && r.value.includes("Local service: running"), String(r.value));
  check("engine switcher has This PC + Cloud", r.value && r.value.includes('"engineBtns":2'), String(r.value));
  check("Epsilon sees the admin-only Accounts section", r.value && r.value.includes('"adminSection":true') && r.value.includes("Epsilon"), String(r.value));
  // PC toggle: turn it on if it is off (needed for the PC flow check below).
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.more-sheet .pill')).find(x=>x.textContent.includes('PC control')); if (b && b.getAttribute('aria-pressed') !== 'true') b.click(); })()`);
  await sleep(1200);
  await closeSettings();

  // 10b. Study tab (0.9.0 Teacher skill): upload, Anki, exams and
  // Listen are all present, and the study endpoints are registered
  // on the local backend (unauthenticated /api/study answers 401,
  // not 404 — the routes exist).
  await openSettingsTab("Study");
  r = await evalJs(`JSON.stringify({headings: Array.from(document.querySelectorAll('.more-sheet h3')).map(h=>h.textContent), uploadLabel: (document.querySelector('.more-sheet .fine')||{}).textContent || '', text: document.querySelector('.more-sheet').textContent})`);
  check("Study tab: files upload, Anki cards, exam countdown, Listen", r.value && r.value.includes("Your study files") && r.value.includes("Upload slides") && r.value.includes("Anki cards") && r.value.includes("Exam countdown") && r.value.includes("Listen"), String((r.value || "").slice(0, 300)));
  r = await evalJs(`fetch(window.ariesDesktop.localApi + '/api/study').then(x => x.status).catch(e => 'ERR:' + e.message)`);
  check("study endpoints registered on the local backend (/api/study is 401, not 404)", r.value === 401, String(r.value));
  await closeSettings();

  // 10c. Connections on the local engine: plain notice that Gmail /
  // Spotify connect through the web app, and Connect is disabled.
  await openSettingsTab("Connections");
  r = await evalJs(`JSON.stringify({notice: (document.querySelector('.more-sheet .notice')||{}).textContent || '', connectDisabled: Array.from(document.querySelectorAll('.more-sheet .conn-actions button')).filter(b=>b.textContent==='Connect').every(b=>b.disabled), openWeb: !!Array.from(document.querySelectorAll('.more-sheet button')).find(b=>b.textContent==='Open the web app')})`);
  check("Connections explains local OAuth honestly, Connect disabled, web link offered", r.value && r.value.includes("connect through the web app") && r.value.includes('"connectDisabled":true') && r.value.includes('"openWeb":true'), String(r.value));
  await closeSettings();

  // 11. PC flow: "open notepad" routes locally. No NVIDIA key on this
  // VM, so the service answers needs_key in plain words (no crash,
  // no raw JSON). That is the honest result here.
  await sendChat("open notepad");
  await sleep(5000);
  r = await evalJs(`JSON.stringify({last: (document.querySelectorAll('.latest-reply p')[0]||{}).textContent?.slice(0,140) || ''})`);
  check("PC message routes locally, plain-words answer (needs key here)", r.value && (r.value.includes("engine key") || r.value.includes("PC control")), String(r.value));

  // 12. Local chat: no engine key on this VM, so the local backend
  // answers with its honest key error. That proves the chat went to
  // the local backend, not the cloud — and the key-missing path must
  // now offer the one-tap switch to the cloud instead of only
  // telling the user to paste a key. "hey" is a greeting, so it is
  // search-exempt: a topic message would stop at the web-search
  // permission prompt first, which this test does not answer. The
  // poll requires the exact chat-error string, not just "engine
  // key" — the earlier PC-flow reply contains that phrase too, so a
  // loose match would pass on a stale reply.
  await sendChat("hey");
  let sawKeyNotice = false;
  let switchShown = false;
  for (let i = 0; i < 15; i++) {
    await sleep(2000);
    r = await evalJs(`JSON.stringify({reply: (document.querySelectorAll('.latest-reply p')[0]||{}).textContent || '', notice: (document.querySelector('.notice')||{}).textContent || '', cloudSwitch: !!Array.from(document.querySelectorAll('.dock button')).find(b=>b.textContent==='Use the cloud instead')})`);
    if (r.value && r.value.includes("No engine key on this PC")) { sawKeyNotice = true; switchShown = r.value.includes('"cloudSwitch":true'); break; }
  }
  check("local chat answers honestly about the missing engine key", sawKeyNotice, String((r.value || "").slice(0, 160)));
  check("key-missing chat offers the one-tap 'Use the cloud instead' switch", switchShown, String((r.value || "").slice(0, 160)));

  // 13. Click the switch: engine flips to cloud, the app reloads,
  // and the login screen shows the cloud choice selected.
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.dock button')).find(x=>x.textContent==='Use the cloud instead'); if (b) b.click(); })()`);
  await sleep(5000);
  s = await authScreenState();
  check("cloud switch reloads to the login screen on the cloud engine", s.includes('"auth":true') && s.includes('"app":false') && s.includes("in the cloud") && s.includes('"stored":"cloud"'), s);

  // 14. Register Kelfy on the cloud engine — with NO keys pasted
  // anywhere. Fresh account, NOT admin, isolated from Epsilon's
  // data (Memory tab says "No memories yet"). Per-engine accounts
  // stay separate: her cloud account is not Epsilon's This-PC one.
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.link-btn')).find(x=>x.textContent.includes('Create one')); if (b) b.click(); })()`);
  await sleep(500);
  await fillAuth("Kelfy", "testpass123");
  await evalJs(`document.querySelector('.auth-btn').click()`);
  await sleep(4000);
  r = await evalJs(`JSON.stringify({app: !!document.querySelector('.aries-app')})`);
  check("Kelfy registers on the cloud with no keys pasted and the app opens", r.value && r.value.includes('"app":true'), String(r.value));
  await openSettingsTab("More");
  r = await evalJs(`JSON.stringify({account: (Array.from(document.querySelectorAll('.more-sheet section')).find(s=>s.textContent.includes('Signed in as'))||{}).textContent?.slice(0,80) || '', adminSection: !!Array.from(document.querySelectorAll('.more-sheet h3')).find(h=>h.textContent==='Accounts'), engineLine: (Array.from(document.querySelectorAll('.more-sheet .fine')).find(p=>p.textContent.includes('Engine:'))||{}).textContent || ''})`);
  check("Kelfy is signed in on the cloud and sees no admin-only Accounts section", r.value && r.value.includes("Kelfy") && r.value.includes('"adminSection":false') && r.value.includes("Engine: Cloud"), String(r.value));
  await closeSettings();
  await openSettingsTab("Memory");
  r = await evalJs(`document.querySelector('.more-sheet').textContent.includes('No memories yet')`);
  check("Kelfy starts with no memories (isolated from Epsilon)", r.value === true, String(r.value));
  await closeSettings();

  // 15. Cloud chat round-trips: the request reaches a backend and a
  // reply comes back. Here the "cloud" is the local backend (test
  // fixture) and this VM has no key, so the honest answer is the
  // key error — on the real cloud the key is configured and the same
  // request gets a model reply. No key-paste switch is offered on
  // the cloud engine, because the cloud needs no key from the user.
  // "hey" again, for the same permission-prompt reason as check 12.
  await sendChat("hey");
  let sawCloudReply = false;
  let noSwitchOnCloud = false;
  for (let i = 0; i < 15; i++) {
    await sleep(2000);
    r = await evalJs(`JSON.stringify({reply: (document.querySelectorAll('.latest-reply p')[0]||{}).textContent || '', cloudSwitch: !!Array.from(document.querySelectorAll('.dock button')).find(b=>b.textContent==='Use the cloud instead')})`);
    if (r.value && r.value.includes("No engine key on this PC")) { sawCloudReply = true; noSwitchOnCloud = r.value.includes('"cloudSwitch":false'); break; }
  }
  check("cloud chat round-trips and returns a reply", sawCloudReply, String((r.value || "").slice(0, 160)));
  check("no key-paste switch is offered on the cloud engine", noSwitchOnCloud, String((r.value || "").slice(0, 160)));

  // 16. Stored preference survives a reload: still Kelfy, still on
  // the cloud engine (Fetch interception stays armed across the
  // reload, so the local backend answers again).
  await send("Page.reload", { ignoreCache: true });
  await sleep(5000);
  r = await evalJs(`JSON.stringify({app: !!document.querySelector('.aries-app'), stored: window.localStorage.getItem('ariesEngine')})`);
  check("stored cloud preference survives a reload (still signed in, still cloud)", r.value && r.value.includes('"app":true') && r.value.includes('"stored":"cloud"'), String(r.value));

  // 17. Log out -> login screen again; log back in on the cloud.
  await openSettingsTab("More");
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.more-sheet button')).find(x=>x.textContent==='Log out'); if (b) b.click(); })()`);
  await sleep(2500);
  r = await evalJs(`JSON.stringify({auth: !!document.querySelector('.auth-screen'), app: !!document.querySelector('.aries-app')})`);
  check("logout returns to the login screen", r.value && r.value.includes('"auth":true') && r.value.includes('"app":false'), String(r.value));
  await fillAuth("Kelfy", "testpass123");
  await evalJs(`document.querySelector('.auth-btn').click()`);
  await sleep(4000);
  r = await evalJs(`JSON.stringify({app: !!document.querySelector('.aries-app')})`);
  check("Kelfy logs back in on the cloud", r.value && r.value.includes('"app":true'), String(r.value));

  ws.close();
  console.log(failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.log("FAIL  harness error:", e.message); process.exit(1); });
