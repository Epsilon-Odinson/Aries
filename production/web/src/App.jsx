import { useCallback, useEffect, useRef, useState } from "react";
import DitherVeil from "./DitherVeil.jsx";
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

function speak(text, on) {
  if (!on || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 1;
  window.speechSynthesis.speak(u);
}

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [messages, setMessages] = useState([]);
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
  // Grain toggle drives DitherVeil's dither cell size: fine = 2px, coarse = 4px.
  const [grain, setGrain] = useState("fine");
  const wakeRef = useRef(null);
  const inputRef = useRef(null);


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

  return (
    <div className="aries-app">
      <div className="aries-veil">
        <DitherVeil
          pattern="floyd"
          pixelSize={grain === "fine" ? 2 : 4}
          inkColor="#120f17"
          paperColor="#f4f1ea"
          revealRadius={200}
          softness={0.6}
          linger={1}
          rim={0.15}
          rimColor="#d9a441"
          fit="cover"
        />
      </div>
      <button
        className="veil-toggle"
        onClick={(e) => {
          e.stopPropagation();
          setGrain(grain === "fine" ? "coarse" : "fine");
        }}
      >
        {grain === "fine" ? "Coarse grain" : "Fine grain"}
      </button>

      {locked ? (
        <LockScreen data={data} onUnlock={() => setLocked(false)} />
      ) : mini ? (
        <MiniPanel
          faceState={faceState}
          status={status}
          input={input}
          setInput={setInput}
          onSend={() => send()}
          onMic={startDictation}
          listening={listening}
          talkBack={talkBack}
          setTalkBack={setTalkBack}
          wakeOn={wakeOn}
          setWakeOn={setWakeOn}
          onExpand={() => setMini(false)}
          onMore={() => { setMini(false); setShowMore(true); }}
          due={due}
          onDismissDue={dismissDue}
          lastReply={messages.filter((m) => m.role === "aries").slice(-1)[0]}
        />
      ) : (
        <main className="aries-hero">
          <div className="aries-lockup">
            <Face state={faceState} size={320} onClick={() => setMini(true)} />
            <h1>
              <span className="word-aries">ARIES</span>
              <span className="word-active">active</span>
              <span className="cook">Ready to cook.</span>
            </h1>
            <p className="status-line">
              <span className="status-dot" /> {status}
            </p>
          </div>

          {due.length > 0 && (
            <div className="due-card" role="alert">
              <strong>Reminder:</strong> {due[0].title}
              <button onClick={() => dismissDue(due[0])}>Done</button>
              <button onClick={() => dismissDue(due[0])}>Dismiss</button>
            </div>
          )}

          <LatestReply messages={messages} />

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
          <div className="hero-tools">
            <button onClick={() => { setMoreTab("personality"); setShowMore(true); }}>
              Settings / More
            </button>
            <label className="toggle">
              <input type="checkbox" checked={talkBack} onChange={(e) => setTalkBack(e.target.checked)} />
              Talk back
            </label>
            <label className="toggle">
              <input type="checkbox" checked={wakeOn} onChange={(e) => setWakeOn(e.target.checked)} />
              Wake phrase (“Hey Aries”)
            </label>
          </div>
          {notice && <p className="notice" onClick={() => setNotice("")}>{notice} ✕</p>}
        </main>
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

function LatestReply({ messages }) {
  const last = [...messages].reverse().find((m) => m.role === "aries");
  if (!last) return <p className="latest-hint">Ask Aries anything — or tap the mic and talk.</p>;
  return (
    <div className="latest-reply">
      <p>{last.content}</p>
      {last.sources?.length > 0 && <Sources sources={last.sources} />}
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

function MiniPanel(props) {
  return (
    <div className="mini-panel">
      <Face state={props.faceState} size={150} onClick={props.onExpand} label="Aries mini — tap to expand" />
      <p className="mini-status">ARIES — {props.status}</p>
      {props.lastReply && <p className="mini-reply">{props.lastReply.content}</p>}
      {props.due.length > 0 && (
        <div className="due-card mini">
          <strong>Reminder:</strong> {props.due[0].title}
          <button onClick={() => props.onDismissDue(props.due[0])}>Done</button>
        </div>
      )}
      <div className="composer-row mini">
        <input
          value={props.input}
          onChange={(e) => props.setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") props.onSend(); }}
          placeholder="Message…"
          aria-label="Message Aries"
        />
        <button className="mic" onClick={props.onMic} title="Dictate">{props.listening ? "●" : "🎙"}</button>
        <button className="send" onClick={props.onSend}>Send</button>
      </div>
      <div className="mini-tools">
        <label className="toggle">
          <input type="checkbox" checked={props.talkBack} onChange={(e) => props.setTalkBack(e.target.checked)} /> Talk back
        </label>
        <label className="toggle">
          <input type="checkbox" checked={props.wakeOn} onChange={(e) => props.setWakeOn(e.target.checked)} /> Wake phrase
        </label>
        <button onClick={props.onMore}>More</button>
        <button onClick={props.onExpand}>Expand</button>
      </div>
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
      <Face state="idle" size={200} />
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

  const tabs = ["personality", "memory", "tasks", "permissions", "face", "screenshot", "history", "installer"];
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

