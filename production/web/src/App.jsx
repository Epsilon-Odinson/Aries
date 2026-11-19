import { useCallback, useEffect, useRef, useState } from "react";
import Veil from "./Veil.jsx";
import Face from "./Face.jsx";
import { api } from "./api.js";

const PERM_LABELS = {
  microphone: "Microphone",
  web_search: "Web search",
  save_memory: "Save memories",
  delete_memory: "Delete / forget memories",
  clear_chat: "Clear chat history",
  screenshot: "Read screenshots",
  face_login: "Face login",
};

// Talk-back speaks in a masculine English voice — the same kind of fluent
// narrator voice as the study-lesson audios. The browser supplies the
// voices, so pick the best masculine English one available and fall back
// to any English voice that doesn't read female.
const MALE_VOICE_HINTS = ["male", "david", "daniel", "james", "guy", "brian", "alex", "fred", "george", "mark", "aaron", "oliver", "samuel"];
const FEMALE_VOICE_HINTS = ["female", "samantha", "victoria", "karen", "moira", "tessa", "zira", "aria", "susan", "jenny", "sonia", "fiona", "kate", "serena", "linda"];

let cachedVoices = [];
function loadVoices() {
  try {
    cachedVoices = window.speechSynthesis.getVoices() || [];
  } catch { /* ignore */ }
}
if ("speechSynthesis" in window) {
  loadVoices();
  window.speechSynthesis.onvoiceschanged = loadVoices;
}

// The voice the user picked in Settings → Voice, stored per device
// (voices differ by device, so this lives in localStorage, not the server).
let savedVoiceName = null;
try { savedVoiceName = window.localStorage.getItem("ariesVoice") || null; } catch { /* ignore */ }

function setSavedVoiceName(name) {
  savedVoiceName = name || null;
  try {
    if (name) window.localStorage.setItem("ariesVoice", name);
    else window.localStorage.removeItem("ariesVoice");
  } catch { /* ignore */ }
}

function pickVoice() {
  let vs = cachedVoices;
  if (!vs.length) {
    try { vs = window.speechSynthesis.getVoices() || []; } catch { vs = []; }
  }
  // The user's own pick wins when that voice exists on this device.
  if (savedVoiceName) {
    const chosen = vs.find((v) => v.name === savedVoiceName);
    if (chosen) return chosen;
  }
  const isEnglish = (v) => /^en/i.test(v.lang || "");
  const lower = (v) => (v.name || "").toLowerCase();
  const male = vs.find((v) => isEnglish(v) && MALE_VOICE_HINTS.some((h) => lower(v).includes(h)));
  if (male) return male;
  return vs.find((v) => isEnglish(v) && !FEMALE_VOICE_HINTS.some((h) => lower(v).includes(h))) || null;
}

function speak(text, on) {
  if (!on || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const v = pickVoice();
  if (v) u.voice = v;
  u.rate = 1;
  u.pitch = 0.9;
  window.speechSynthesis.speak(u);
}

// Aries' replies appear as you read them — word by word, like he's
// thinking as he speaks. A short beat at a comma, a longer beat at a
// full stop. Never a whole wall of text dumped at once. Tap the reply
// to show it all instantly.
function useTypewriter(text) {
  const [shown, setShown] = useState("");
  const [done, setDone] = useState(true);
  const timerRef = useRef(null);
  const cancelRef = useRef(false);

  useEffect(() => {
    if (!text) { setShown(""); setDone(true); return; }
    const tokens = text.match(/\S+\s*/g) || [];
    let i = 0;
    cancelRef.current = false;
    setShown("");
    setDone(false);
    const step = () => {
      if (cancelRef.current) return;
      i += 1;
      setShown(tokens.slice(0, i).join(""));
      if (i >= tokens.length) { setDone(true); return; }
      const last = tokens[i - 1].trim().slice(-1);
      let delay = 55;
      if (".!?".includes(last)) delay = 320;
      else if (",;:".includes(last)) delay = 150;
      timerRef.current = setTimeout(step, delay);
    };
    timerRef.current = setTimeout(step, 150);
    return () => { cancelRef.current = true; clearTimeout(timerRef.current); };
  }, [text]);

  const finish = () => {
    cancelRef.current = true;
    clearTimeout(timerRef.current);
    setShown(text || "");
    setDone(true);
  };

  return { shown, done, finish };
}

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [messages, setMessages] = useState([]);
  const lastAries = messages.filter((m) => m.role === "aries").slice(-1)[0];
  const typed = useTypewriter(lastAries?.content || "");
  const [input, setInput] = useState("");
  const [faceState, setFaceState] = useState("idle");
  const [status, setStatus] = useState("READY");
  const [useSearch, setUseSearch] = useState(false);
  const [showMore, setShowMore] = useState(false);
  const [moreTab, setMoreTab] = useState("personality");
  const [mini, setMini] = useState(false);
  const [talkBack, setTalkBack] = useState(true);
  const [wakeOn, setWakeOn] = useState(false);
  const [listening, setListening] = useState(false);
  const [permPrompt, setPermPrompt] = useState(null);
  const [due, setDue] = useState([]);
  const [locked, setLocked] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [notice, setNotice] = useState("");
  // Grain toggle swaps which baked grain image the veil shows.
  const [grain, setGrain] = useState("fine");
  // Burst button: each increment fires one burst from the veil's centre.
  const [burstSignal, setBurstSignal] = useState(0);
  const wakeRef = useRef(null);
  const inputRef = useRef(null);

  // Mini mode is the web front's body class: the visor shrinks, the
  // greeting and status fade out, and the stage moves to the bottom.
  // Pressing the face toggles it; Expand returns to full size.
  useEffect(() => {
    document.body.classList.toggle("mini", mini);
    return () => document.body.classList.remove("mini");
  }, [mini]);

  const load = useCallback(async () => {
    try {
      const s = await api.state();
      setData(s);
      setMessages(s.messages || []);
      if (s.face?.enrolled && s.permissions?.face_login === "always") setLocked(true);
      if (!localStorage.getItem("aries-setup-done") && !s.profile?.name) setSetupOpen(true);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(async () => {
      try {
        const d = await api.dueTasks();
        if (d.length) setDue(d);
      } catch { /* ignore */ }
    }, 30000);
    return () => clearInterval(t);
  }, [load]);

  function askPermission(capability, why) {
    return new Promise((resolve) => {
      setPermPrompt({ capability, why, resolve });
    });
  }
  function answerPerm(choice) {
    const p = permPrompt;
    if (!p) return;
    setPermPrompt(null);
    if (choice === "deny") return p.resolve(false);
    if (choice === "always") {
      api.savePermission(p.capability, "always").catch(() => {});
      load();
    }
    p.resolve(true);
  }

  async function send(text, opts = {}) {
    const msg = (text ?? input).trim();
    if (!msg) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: msg }]);
    setFaceState("thinking");
    setStatus("THINKING");
    try {
      let res = await api.chat(msg, opts.useSearch ?? useSearch, opts.consent || {});
      if (res.needs_permission) {
        const ok = await askPermission(
          res.needs_permission,
          `Aries wants to use ${PERM_LABELS[res.needs_permission] || res.needs_permission} to answer this.`
        );
        if (!ok) {
          setFaceState("idle");
          setStatus("READY");
          return;
        }
        res = await api.chat(msg, opts.useSearch ?? useSearch, {
          [res.needs_permission]: true,
        });
      }
      setFaceState(res.searched ? "idle" : "speaking");
      setStatus(res.searched ? "READY" : "SPEAKING");
      setMessages((m) => [
        ...m,
        { role: "aries", content: res.reply, sources: res.sources || [] },
      ]);
      if (res.pending_memory && !res.pending_memory.saved) {
        setNotice(`Aries learned: "${res.pending_memory.text}" — approve it in Memory.`);
      }
      if (res.task_added) setNotice(`Task kept: ${res.task_added.title}`);
      speak(res.reply, talkBack);
      setTimeout(() => {
        setFaceState("idle");
        setStatus("READY");
      }, 2500);
      load();
    } catch (e) {
      setFaceState("error");
      setStatus("ERROR");
      setMessages((m) => [...m, { role: "aries", content: `Something went wrong: ${e.message}` }]);
    }
  }

  function startDictation() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      setNotice("This browser has no speech recognition. Use your keyboard's mic key instead — it dictates straight into the box.");
      return;
    }
    const rec = new SR();
    rec.lang = "en-US";
    rec.interimResults = false;
    rec.onresult = (e) => {
      const text = e.results[0][0].transcript;
      setInput((v) => (v ? `${v} ${text}` : text));
      inputRef.current?.focus();
    };
    rec.onerror = (e) => {
      setListening(false);
      if (e.error === "not-allowed") {
        setNotice("Microphone is blocked for this site. In Chrome: lock/tune icon → Site settings → Microphone → Allow, then reload. Or use your keyboard's mic key.");
      } else if (e.error === "no-speech") {
        setNotice("Didn't catch that — try again.");
      } else {
        setNotice(`Mic error: ${e.error}. Typing works as normal.`);
      }
    };
    rec.onend = () => setListening(false);
    try {
      rec.start();
      setListening(true);
      setFaceState("listening");
      setStatus("LISTENING");
    } catch {
      setListening(false);
    }
  }

  useEffect(() => {
    if (!wakeOn) {
      wakeRef.current?.stop?.();
      wakeRef.current = null;
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      setNotice("Wake phrase needs speech recognition, which this browser doesn't have.");
      setWakeOn(false);
      return;
    }
    const rec = new SR();
    rec.lang = "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e) => {
      const said = Array.from(e.results).map((r) => r[0].transcript).join(" ").toLowerCase();
      if (said.includes("hey aries")) {
        setMini(false);
        const greet = "Aries active. Ready to cook.";
        setMessages((m) => [...m, { role: "aries", content: greet }]);
        speak(greet, true);
        setTimeout(startDictation, 900);
      }
    };
    rec.onerror = () => {};
    rec.onend = () => {
      if (wakeRef.current === rec) {
        try { rec.start(); } catch { /* stopped */ }
      }
    };
    wakeRef.current = rec;
    try { rec.start(); } catch { /* ignore */ }
    return () => {
      try { rec.stop(); } catch { /* ignore */ }
      wakeRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeOn]);

  async function dismissDue(task) {
    await api.markReminded(task.id).catch(() => {});
    setDue((d) => d.filter((t) => t.id !== task.id));
  }

  if (error && !data) {
    return (
      <div className="aries-error">
        <h1>ARIES</h1>
        <p>Could not reach the Aries server: {error}</p>
        <p>Check the backend is running, then reload.</p>
      </div>
    );
  }
  if (!data) return <div className="aries-loading">ARIES — waking up…</div>;

  const hint = mini
    ? "Mini mode. Press the face again to bring Aries back to full size."
    : "Move across the veil to lift it — the full colour is underneath. Tap for a burst. Press the face for mini mode.";

  return (
    <div className="aries-app">
      <Veil grain={grain} burstSignal={burstSignal} />

      <div className="topline">
        <span className="state-label">ARIES</span>
      </div>

      {locked ? (
        <LockScreen data={data} onUnlock={() => setLocked(false)} />
      ) : (
        <main className="stage">
          <Face
            state={faceState}
            onClick={() => setMini((v) => !v)}
            label={mini ? "Aries face, mini mode. Press to return to full size." : "Aries face. Press to toggle mini mode."}
          />
          <div className="greeting">
            <h1>
              <span className="word-aries">ARIES</span>
              <span className="word-active">active</span>
              <span className="cook">Ready to cook.</span>
            </h1>
            <p className="status">
              <span className="dot" aria-hidden="true" />
              <span>{status}</span>
            </p>
          </div>

          {due.length > 0 && (
            <div className="due-card" role="alert">
              <strong>Reminder:</strong> {due[0].title}
              <button onClick={() => dismissDue(due[0])}>Done</button>
              <button onClick={() => dismissDue(due[0])}>Dismiss</button>
            </div>
          )}

          <LatestReply message={lastAries} typed={typed} />
        </main>
      )}

      {!locked && (
        <div className="dock">
          <p className="hint">{hint}</p>
          {notice && <p className="notice" onClick={() => setNotice("")}>{notice} ✕</p>}
          <div className="composer-row">
            <button
              className={`globe ${useSearch ? "on" : ""}`}
              onClick={() => setUseSearch((v) => !v)}
              title="Search the web for the next message"
              aria-pressed={useSearch}
            >🌐</button>
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") send(); }}
              placeholder="Type a message…"
              aria-label="Message Aries"
            />
            <button className="mic" onClick={startDictation} title="Dictate with microphone" aria-label="Dictate with microphone">
              {listening ? "●" : "🎙"}
            </button>
            <button className="send" onClick={() => send()}>Send</button>
          </div>
          <div className="controls">
            <button className="pill" type="button" onClick={() => setBurstSignal((n) => n + 1)}>
              Release a burst
            </button>
            <div className="grain-group" role="group" aria-label="Veil grain">
              <button className="pill" type="button" aria-pressed={grain === "fine"} onClick={() => setGrain("fine")}>
                Fine grain
              </button>
              <button className="pill" type="button" aria-pressed={grain === "coarse"} onClick={() => setGrain("coarse")}>
                Coarse grain
              </button>
            </div>
            <button
              className="pill"
              type="button"
              aria-pressed={useSearch}
              onClick={() => setUseSearch((v) => !v)}
              title="Search the web for the next message"
            >
              Search the web
            </button>
            <button
              className="pill"
              type="button"
              aria-pressed={talkBack}
              onClick={() => setTalkBack((v) => !v)}
            >
              Talk back
            </button>
            <button
              className="pill"
              type="button"
              aria-pressed={wakeOn}
              onClick={() => setWakeOn((v) => !v)}
            >
              Wake phrase (“Hey Aries”)
            </button>
            <button
              className="pill"
              type="button"
              onClick={() => { setMoreTab("personality"); setShowMore(true); }}
            >
              Settings / More
            </button>
            {mini && (
              <button className="pill" type="button" onClick={() => setMini(false)}>
                Expand
              </button>
            )}
          </div>
        </div>
      )}

      {permPrompt && (
        <div className="perm-modal" role="dialog" aria-label="Permission request">
          <div className="perm-card">
            <h2>Aries is asking permission</h2>
            <p>{permPrompt.why}</p>
            <div className="perm-actions">
              <button onClick={() => answerPerm("once")}>Allow once</button>
              <button onClick={() => answerPerm("always")}>Allow always</button>
              <button onClick={() => answerPerm("deny")}>Deny</button>
            </div>
          </div>
        </div>
      )}

      {showMore && (
        <MoreSheet
          data={data}
          tab={moreTab}
          setTab={setMoreTab}
          onClose={() => { setShowMore(false); load(); }}
        />
      )}

      {setupOpen && (
        <SetupWizard
          onDone={() => { localStorage.setItem("aries-setup-done", "1"); setSetupOpen(false); load(); }}
          onSkip={() => { localStorage.setItem("aries-setup-done", "1"); setSetupOpen(false); }}
        />
      )}
    </div>
  );
}

function LatestReply({ message, typed }) {
  if (!message) return <p className="latest-hint">Ask Aries anything — or tap the mic and talk.</p>;
  return (
    <div className="latest-reply">
      <p onClick={typed.finish} title="Tap to show the full reply">
        {typed.shown}
        {!typed.done && <span className="type-caret">▍</span>}
      </p>
      {typed.done && message.sources?.length > 0 && <Sources sources={message.sources} />}
    </div>
  );
}

function Sources({ sources }) {
  return (
    <div className="sources">
      <strong>Sources</strong>
      <ul>
        {sources.map((s, i) => (
          <li key={i}>
            <a href={s.url} target="_blank" rel="noreferrer">
              {s.title || s.url}
            </a>
            {s.publisher ? ` — ${s.publisher}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function LockScreen({ data, onUnlock }) {
  const [file, setFile] = useState(null);
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState("Aries is locked. Show your face, or enter your PIN.");
  async function verify() {
    if (!file) { setMsg("Choose a photo of your face first."); return; }
    setMsg("Checking…");
    try {
      const r = await api.verifyFace(file);
      if (r.match) { setMsg("Welcome back."); onUnlock(); }
      else setMsg("That face doesn't match. Try again, or use your PIN.");
    } catch (e) { setMsg(e.message); }
  }
  async function unlockPin() {
    try {
      const r = await api.unlockPin(pin);
      if (r.ok) onUnlock();
      else setMsg("Wrong PIN.");
    } catch (e) { setMsg(e.message); }
  }
  return (
    <div className="lock-screen">
      <Face state="idle" />
      <h1>ARIES</h1>
      <p>{msg}</p>
      <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files[0])} />
      <button onClick={verify}>Unlock with my face</button>
      <div className="pin-row">
        <input type="password" value={pin} onChange={(e) => setPin(e.target.value)} placeholder="Unlock PIN" />
        <button onClick={unlockPin}>Unlock with PIN</button>
      </div>
      <p className="fine">Face login is a convenience lock (OpenCV LBPH), not biometric-grade security.</p>
    </div>
  );
}

// Let the user pick Aries' talk-back voice himself, from the voices on
// this device. Tap Hear to try one, tap Use this voice to make Aries
// speak with it. Saved per device in localStorage.
function VoicePicker() {
  const [voices, setVoices] = useState([]);
  const [selected, setSelected] = useState(savedVoiceName);
  const [previewing, setPreviewing] = useState("");

  useEffect(() => {
    const refresh = () => {
      try {
        const vs = (window.speechSynthesis.getVoices() || []).filter((v) => /^en/i.test(v.lang || ""));
        setVoices(vs);
      } catch { /* ignore */ }
    };
    refresh();
    if ("speechSynthesis" in window) {
      window.speechSynthesis.addEventListener("voiceschanged", refresh);
      return () => window.speechSynthesis.removeEventListener("voiceschanged", refresh);
    }
  }, []);

  const preview = (v) => {
    if (!("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance("Aries active. Ready to cook.");
    u.voice = v;
    u.rate = 1;
    u.pitch = 0.9;
    setPreviewing(v.name);
    u.onend = () => setPreviewing("");
    u.onerror = () => setPreviewing("");
    window.speechSynthesis.speak(u);
  };

  const choose = (name) => {
    setSavedVoiceName(name);
    setSelected(name);
  };

  return (
    <section>
      <h3>Talk-back voice</h3>
      <p className="fine">These are the voices on this device. Tap Hear to try one, then tap Use this voice to make Aries speak with it. Your pick is saved on this device only.</p>
      <p>Now using: <strong>{selected || "Aries' default (masculine)"}</strong></p>
      <button onClick={() => choose(null)}>Use Aries' default</button>
      {voices.length === 0 && <p>No English voices found on this device yet — try closing and reopening Settings.</p>}
      <ul className="voice-list">
        {voices.map((v) => (
          <li key={v.name + v.lang} className={selected === v.name ? "on" : ""}>
            <span>{v.name} <span className="fine">({v.lang})</span></span>
            <span>
              <button onClick={() => preview(v)}>{previewing === v.name ? "Playing…" : "Hear"}</button>
              <button onClick={() => choose(v.name)}>{selected === v.name ? "✓ Using" : "Use this voice"}</button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function MoreSheet({ data, tab, setTab, onClose }) {
  const [person, setPerson] = useState(data.personality);
  const [profile, setProfile] = useState(data.profile);
  const [memText, setMemText] = useState("");
  const [memFilter, setMemFilter] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDue, setTaskDue] = useState("");
  const [shotFile, setShotFile] = useState(null);
  const [shotQ, setShotQ] = useState("");
  const [shotDesc, setShotDesc] = useState("");
  const [faceFile, setFaceFile] = useState(null);
  const [facePin, setFacePin] = useState("");
  const [msg, setMsg] = useState("");
  const [perms, setPerms] = useState(data.permissions);

  const memories = (data.memories || []).filter((m) =>
    !memFilter || m.text.toLowerCase().includes(memFilter.toLowerCase())
  );

  async function savePerson() {
    await api.savePersonality(person);
    setMsg("Personality saved.");
  }
  async function saveProfile() {
    await api.saveProfile(profile);
    setMsg("Profile saved.");
  }
  async function addMem() {
    if (!memText.trim()) return;
    await api.addMemory(memText.trim(), "general");
    setMemText("");
    setMsg("Memory saved.");
    onClose();
  }
  async function addTask() {
    if (!taskTitle.trim()) return;
    await api.addTask({ title: taskTitle.trim(), due_at: taskDue || null });
    setTaskTitle("");
    setTaskDue("");
    setMsg("Task added.");
    onClose();
  }
  async function describeShot() {
    if (!shotFile) { setMsg("Choose an image first."); return; }
    setMsg("Looking at the image…");
    try {
      const r = await api.describeScreenshot(shotFile, shotQ, true);
      setShotDesc(r.description);
      setMsg("");
    } catch (e) { setMsg(e.message); }
  }
  async function enroll() {
    if (!faceFile) { setMsg("Choose a photo of your face first."); return; }
    setMsg("Enrolling…");
    try {
      await api.enrollFace(faceFile, profile.name, facePin);
      setMsg("Face enrolled. Face login is on.");
    } catch (e) { setMsg(e.message); }
  }

  const tabs = ["personality", "voice", "memory", "tasks", "permissions", "face", "screenshot", "history", "installer"];
  return (
    <div className="more-sheet" role="dialog" aria-label="Settings and more">
      <div className="more-head">
        <h2>Settings / More</h2>
        <button onClick={onClose}>Close ✕</button>
      </div>
      <div className="more-tabs">
        {tabs.map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      {msg && <p className="notice">{msg}</p>}

      {tab === "personality" && (
        <section>
          <h3>Personality</h3>
          <label>Voice
            <select value={person.voice} onChange={(e) => setPerson({ ...person, voice: e.target.value })}>
              {["companion", "mentor", "hype", "calm"].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
          {["warmth", "directness", "humour"].map((k) => (
            <label key={k}>{k} — {person[k]}
              <input type="range" min="0" max="100" value={person[k]}
                onChange={(e) => setPerson({ ...person, [k]: Number(e.target.value) })} />
            </label>
          ))}
          <label>Character notes
            <textarea value={person.notes} onChange={(e) => setPerson({ ...person, notes: e.target.value })}
              placeholder="How should Aries act?" rows="3" />
          </label>
          <button onClick={savePerson}>Save personality</button>
          <h3>Profile</h3>
          <label>Name <input value={profile.name} onChange={(e) => setProfile({ ...profile, name: e.target.value })} /></label>
          <label>Call me <input value={profile.call_name} onChange={(e) => setProfile({ ...profile, call_name: e.target.value })} /></label>
          <label>About me
            <textarea value={profile.about} onChange={(e) => setProfile({ ...profile, about: e.target.value })} rows="3" />
          </label>
          <button onClick={saveProfile}>Save profile</button>
        </section>
      )}

      {tab === "voice" && <VoicePicker />}

      {tab === "memory" && (
        <section>
          <h3>Memory vault</h3>
          <input value={memFilter} onChange={(e) => setMemFilter(e.target.value)} placeholder="Filter memories…" />
          <div className="mem-add">
            <input value={memText} onChange={(e) => setMemText(e.target.value)} placeholder="Teach Aries something…" />
            <button onClick={addMem}>Remember</button>
          </div>
          <ul className="mem-list">
            {memories.map((m) => (
              <li key={m.id} className={m.pending ? "pending" : ""}>
                <span>{m.pending ? "⏳ Awaiting approval — " : ""}{m.pinned ? "📌 " : ""}{m.text}</span>
                <span className="mem-actions">
                  {m.pending && <button onClick={() => api.approveMemory(m.id).then(onClose)}>Approve</button>}
                  <button onClick={() => api.pinMemory(m.id).then(onClose)}>{m.pinned ? "Unpin" : "Pin"}</button>
                  <button onClick={() => { if (confirm("Forget this memory?")) api.deleteMemory(m.id).then(onClose); }}>Forget</button>
                </span>
              </li>
            ))}
          </ul>
          {memories.length === 0 && <p>No memories yet. Teach Aries something above, or say “remember …” in chat.</p>}
        </section>
      )}

      {tab === "tasks" && (
        <section>
          <h3>Tasks &amp; reminders</h3>
          <div className="task-add">
            <input value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} placeholder="New task…" />
            <input type="datetime-local" value={taskDue} onChange={(e) => setTaskDue(e.target.value)} />
            <button onClick={addTask}>Add</button>
          </div>
          <ul className="task-list">
            {(data.tasks || []).map((t) => (
              <li key={t.id} className={t.done ? "done" : ""}>
                <span>{t.done ? "✓ " : ""}{t.title}{t.due_at ? ` — due ${new Date(t.due_at).toLocaleString()}` : ""}</span>
                <span>
                  <button onClick={() => api.toggleTask(t.id).then(onClose)}>{t.done ? "Undo" : "Done"}</button>
                  <button onClick={() => { if (confirm("Delete this task?")) api.deleteTask(t.id).then(onClose); }}>Delete</button>
                </span>
              </li>
            ))}
          </ul>
          <p className="fine">Tip: in chat, say “remind me to call a friend tomorrow at 9am” and Aries saves it.</p>
        </section>
      )}

      {tab === "permissions" && (
        <section>
          <h3>Permissions</h3>
          <p className="fine">Aries asks before anything sensitive. “Never” hard-blocks on the server.</p>
          {Object.entries(perms).map(([cap, setting]) => (
            <label key={cap}>{PERM_LABELS[cap] || cap}
              <select
                value={setting}
                onChange={async (e) => {
                  const s = e.target.value;
                  await api.savePermission(cap, s);
                  setPerms({ ...perms, [cap]: s });
                }}
              >
                <option value="ask">Ask me</option>
                <option value="always">Always allow</option>
                <option value="never">Never</option>
              </select>
            </label>
          ))}
        </section>
      )}

      {tab === "face" && (
        <section>
          <h3>Face login</h3>
          <p>Status: {data.face?.enrolled ? "Enrolled" : "Not enrolled"}</p>
          <input type="file" accept="image/*" onChange={(e) => setFaceFile(e.target.files[0])} />
          <input type="password" value={facePin} onChange={(e) => setFacePin(e.target.value)} placeholder="Unlock PIN (optional, sets fallback)" />
          <button onClick={enroll}>Enroll my face</button>
          {data.face?.enrolled && <button onClick={() => { if (confirm("Remove face enrollment?")) api.removeFace().then(onClose); }}>Remove enrollment</button>}
          <p className="fine">OpenCV LBPH recognition — a convenience lock, not biometric-grade security. The PIN is the real fallback; only its hash is stored.</p>
        </section>
      )}

      {tab === "screenshot" && (
        <section>
          <h3>Screenshot</h3>
          <p className="fine">Aries asks permission before reading any image.</p>
          <input type="file" accept="image/*" onChange={(e) => setShotFile(e.target.files[0])} />
          <input value={shotQ} onChange={(e) => setShotQ(e.target.value)} placeholder="Ask about the image (optional)" />
          <button onClick={describeShot}>Describe this image</button>
          {shotDesc && <div className="shot-desc"><p>{shotDesc}</p></div>}
        </section>
      )}

      {tab === "history" && (
        <section>
          <h3>Full history</h3>
          <button onClick={async () => { if (confirm("Clear all chat history?")) { await api.clearChat(true); onClose(); } }}>
            Clear chat history
          </button>
          <div className="history">
            {(data.messages || []).map((m) => (
              <div key={m.id} className={`hist-msg ${m.role}`}>
                <strong>{m.role === "user" ? "You" : "Aries"}:</strong> {m.content}
                {m.sources?.length > 0 && <Sources sources={m.sources} />}
              </div>
            ))}
          </div>
        </section>
      )}

      {tab === "installer" && (
        <section>
          <h3>Desktop app &amp; installer</h3>
          <p>The desktop installer is packaged from the PyQt app in the Aries repo
            (<code>aries/</code>). It fills the full screen when activated, is freely
            resizable, and tapping the bot drops it into mini mode floating
            bottom-right, just above the taskbar.</p>
          <p><strong>Engine key:</strong> server-side only. The installer never contains
            an API key — users sign in to the SaaS backend instead. Aries is private
            to Epsilon and a friend until it goes commercial.</p>
          <p><strong>Roadmap:</strong> web search ✓ · voice ✓ · tasks ✓ · installer ✓ ·
            face login ✓ · screenshot ✓ · PC control — last, not included here.</p>
        </section>
      )}
    </div>
  );
}

function SetupWizard({ onDone, onSkip }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [voice, setVoice] = useState("companion");
  const [perms, setPerms] = useState({ web_search: "ask", save_memory: "ask", microphone: "ask" });
  const steps = ["Welcome", "Who you are", "Voice", "Permissions"];
  async function finish() {
    await api.saveProfile({ name, call_name: name, about: "" }).catch(() => {});
    await api.savePersonality({ voice, warmth: 70, directness: 60, humour: 50, notes: "" }).catch(() => {});
    for (const [cap, setting] of Object.entries(perms)) {
      await api.savePermission(cap, setting).catch(() => {});
    }
    onDone();
  }
  return (
    <div className="wizard" role="dialog" aria-label="Setup wizard">
      <div className="wizard-card">
        <h2>Welcome to Aries</h2>
        <p className="fine">Step {step + 1} of {steps.length}: {steps[step]}</p>
        <p className="fine">One thing setup never asks for: an API key. The engine key stays
          server-side only, never in the app and never on your device.</p>
        {step === 1 && (
          <label>What should Aries call you?
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
          </label>
        )}
        {step === 2 && (
          <label>Pick a voice
            <select value={voice} onChange={(e) => setVoice(e.target.value)}>
              {["companion", "mentor", "hype", "calm"].map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        )}
        {step === 3 && (
          <div>
            <p>Permission defaults — change any of these later in Settings → Permissions.</p>
            {Object.entries(perms).map(([cap, s]) => (
              <label key={cap}>{PERM_LABELS[cap] || cap}
                <select value={s} onChange={(e) => setPerms({ ...perms, [cap]: e.target.value })}>
                  <option value="ask">Ask me</option>
                  <option value="always">Always allow</option>
                  <option value="never">Never</option>
                </select>
              </label>
            ))}
          </div>
        )}
        <div className="wizard-actions">
          <button onClick={onSkip}>Skip setup</button>
          {step > 0 && <button onClick={() => setStep(step - 1)}>Back</button>}
          {step < steps.length - 1
            ? <button onClick={() => setStep(step + 1)}>Next</button>
            : <button onClick={finish}>Finish — Aries active. Ready to cook.</button>}
        </div>
      </div>
    </div>
  );
}
