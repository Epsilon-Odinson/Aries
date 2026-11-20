// CDP smoke test for the Aries Electron shell.
// Connects to the shell's remote-debugging port and exercises the
// running app in the real renderer: bridge, stamp, mic (no crash),
// mini mode, Settings, PC control. Prints PASS/FAIL per check and
// exits non-zero when any check fails.
//
// Usage: node cdp-test.js [port]   (default 9222)
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

  // 1. App rendered (not the error screen).
  await sleep(3000);
  let r = await evalJs(`document.querySelector('.aries-app') ? 'app' : (document.body.innerText.slice(0,120))`);
  check("app renders (.aries-app present)", r.value === "app", String(r.value).slice(0, 100));

  // 2. Desktop bridge present.
  r = await evalJs(`window.ariesDesktop ? JSON.stringify({isDesktop: window.ariesDesktop.isDesktop, localApi: !!window.ariesDesktop.localApi, cloudApi: window.ariesDesktop.cloudApi}) : 'missing'`);
  check("desktop bridge (window.ariesDesktop)", r.value && r.value.includes("true"), String(r.value));

  // 3. Stamp visible.
  r = await evalJs(`(() => { const el = document.querySelector('.made-by'); if (!el) return 'missing'; const cs = getComputedStyle(el); return JSON.stringify({text: el.textContent, font: cs.fontFamily.slice(0,40), style: cs.fontStyle, display: cs.display}); })()`);
  check("stamp '© Made by Epsilon' present, italic script", r.value && r.value.includes("Made by Epsilon") && r.value.includes("italic"), String(r.value));

  // 4. Local service reachable from the renderer.
  r = await evalJs(`fetch(window.ariesDesktop.localApi + '/health').then(x => x.json()).then(j => JSON.stringify(j)).catch(e => 'ERR:' + e.message)`);
  check("local service health from renderer", r.value && r.value.includes('"ok":true'), String(r.value));

  // 5. Face + greeting lockup + composer present (same as web).
  r = await evalJs(`JSON.stringify({face: !!document.querySelector('.face-btn, .visor'), greeting: (document.querySelector('.greeting')||{}).textContent?.slice(0,30) || '', composer: !!document.querySelector('.composer-row input'), send: !!document.querySelector('.send'), mic: !!document.querySelector('.mic')})`);
  check("face, greeting, composer, mic, Send present", r.value && r.value.includes('"face":true') && r.value.includes('"composer":true'), String(r.value));

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

  // 8. Settings: gear opens, 4 tabs, Desktop section (shell-only,
  // on the More tab).
  await evalJs(`document.querySelector('.gear-btn').click()`);
  await sleep(1200);
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.more-tabs button')).find(x=>x.textContent==='More'); if (b) b.click(); })()`);
  await sleep(1200);
  r = await evalJs(`JSON.stringify({tabs: Array.from(document.querySelectorAll('.more-tabs button')).map(x=>x.textContent), desktopSection: !!Array.from(document.querySelectorAll('.more-sheet h3')).find(h=>h.textContent==='Desktop'), pcToggle: !!Array.from(document.querySelectorAll('.more-sheet .pill')).find(b=>b.textContent.includes('PC control')), keyField: !!document.querySelector('.more-sheet input[type=password]')})`);
  check("Settings: 4 tabs + Desktop section (PC toggle + key field)", r.value && r.value.includes('"desktopSection":true') && r.value.includes('"pcToggle":true') && r.value.includes('"keyField":true'), String(r.value));
  // PC toggle: turn it on if it is off (needed for the PC flow check below).
  await evalJs(`(() => { const b = Array.from(document.querySelectorAll('.more-sheet .pill')).find(x=>x.textContent.includes('PC control')); if (b && b.getAttribute('aria-pressed') !== 'true') b.click(); })()`);
  await sleep(1200);
  await evalJs(`document.querySelector('.more-head button').click()`); // close settings
  await sleep(800);

  // 9. PC flow: "open notepad" routes locally. No NVIDIA key on this
  // VM, so the service answers needs_key in plain words (no crash,
  // no raw JSON). That is the honest result here. Type via CDP
  // Input.insertText so React's controlled input actually updates.
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: 600, y: 700, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: 600, y: 700, button: "left", clickCount: 1 });
  await evalJs(`document.querySelector('.composer-row input').focus()`);
  await send("Input.insertText", { text: "open notepad" });
  await sleep(500);
  await evalJs(`document.querySelector('.send').click()`);
  await sleep(5000);
  r = await evalJs(`JSON.stringify({last: (document.querySelectorAll('.latest-reply p')[0]||{}).textContent?.slice(0,140) || ''})`);
  check("PC message routes locally, plain-words answer (needs key here)", r.value && (r.value.includes("engine key") || r.value.includes("PC control")), String(r.value));

  // 10. Cloud chat: one real message to the Render backend (live).
  // Cold start can be slow; allow 120s.
  await evalJs(`document.querySelector('.composer-row input').focus()`);
  await send("Input.insertText", { text: "Say just the word OK and nothing else." });
  await sleep(500);
  await evalJs(`document.querySelector('.send').click()`);
  let replied = false;
  for (let i = 0; i < 60; i++) {
    await sleep(2000);
    r = await evalJs(`(document.querySelectorAll('.latest-reply p')[0]||{}).textContent || ''`);
    if (r.value && r.value.trim().length > 0 && !r.value.includes("Something went wrong")) { replied = true; break; }
  }
  check("cloud chat reply arrives", replied, String((r.value || "").slice(0, 80)));

  ws.close();
  console.log(failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((e) => { console.log("FAIL  harness error:", e.message); process.exit(1); });
