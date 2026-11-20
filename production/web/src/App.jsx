import { useCallback, useEffect, useRef, useState } from "react";
import Veil from "./Veil.jsx";
import Face from "./Face.jsx";
import { api, local, isDesktop, getToken, setToken } from "./api.js";

// Desktop shell only: does a message look like a PC action (open an
// app, type, click, shutdown, screenshot, ...)? Port of the Python
// heuristic — those go to the local service (local NVIDIA LLM), since
// the cloud backend does not do PC actions. Everything else goes to
// the cloud chat as normal.
const PC_PATTERNS = [
  /\b(open|launch|start|run)\s+(file\s+explorer|explorer|notepad|calculator|paint|word|excel|powerpoint|chrome|edge|firefox|spotify|vlc|settings|control\s+panel|task\s+manager|cmd|command\s+prompt|terminal|powershell|the\s+(file|folder|app|application|program))\b/i,
  /\b(open|close)\s+(a\s+|the\s+)?(file|folder|app|application|program|window)\b/i,
  /\b(shutdown|restart|reboot|sleep|hibernate|log\s+off|lock\s+(the\s+|my\s+)?(pc|computer|screen))\b/i,
  /\b(take\s+a\s+screenshot|screenshot)\b/i,
  /\b(volume\s+(up|down)|mute|unmute|brightness\s+(up|down))\b/i,
  /\b(type|click|press|move\s+the\s+mouse|scroll)\b/i,
  /\b(delete|create|rename|move|copy)\s+(a\s+|the\s+)?(file|folder)\b/i,
  /[A-Za-z]:\\[^\s]+|\.exe\b|\.txt\b/i,
];
function looksLikePcCommand(text) {
  const t = (text || "").toLowerCase();
  return PC_PATTERNS.some((p) => p.test(t));
}

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

// ElevenLabs talk-back: when the server has a voice key, Aries speaks
// with a real narrator voice (Adam by default) instead of the phone's
// built-in voices. The chosen ElevenLabs voice_id is saved per device
// in localStorage, like the browser-voice pick.
let elevenConfigured = false;
let savedElevenVoiceId = null;
try { savedElevenVoiceId = window.localStorage.getItem("ariesElevenVoice") || null; } catch { /* ignore */ }

function setSavedElevenVoiceId(id) {
  savedElevenVoiceId = id || null;
  try {
    if (id) window.localStorage.setItem("ariesElevenVoice", id);
    else window.localStorage.removeItem("ariesElevenVoice");
  } catch { /* ignore */ }
}

let elevenAudio = null;
let elevenGen = 0;
let browserGen = 0;
function stopAllSpeech() {
  try { window.speechSynthesis.cancel(); } catch { /* ignore */ }
  elevenGen++;
  browserGen++;
  if (elevenAudio) {
    try { elevenAudio.pause(); } catch { /* ignore */ }
    elevenAudio = null;
  }
}

// Split a reply into speakable chunks (sentences, merged so none is
// tiny). The voice starts on the first chunk instead of waiting for
// the whole reply to be synthesised, which is what made talk-back
// feel slow.
function splitSentences(text) {
  const parts = (text || "")
    .split(/(?<=[.!?])\s+|\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const chunks = [];
  let buf = "";
  for (const p of parts) {
    if (buf && buf.length + p.length + 1 > 220) {
      chunks.push(buf);
      buf = p;
    } else {
      buf = buf ? buf + " " + p : p;
    }
  }
  if (buf) chunks.push(buf);
  return chunks.length ? chunks : [text];
}

// Returns "spoke" (talked), "unavailable" (not configured or the
// request failed — the caller falls back to the device's voices), or
// "blocked" (the browser refused to play sound until the user taps
// something — the device fallback would fail the same way, so the
// caller reports it instead of silently falling back and going quiet).
// Speaks chunk by chunk, fetching the next chunk while the current one
// plays, so the first sentence is heard quickly and the rest follow
// without a gap. A failed chunk is skipped, not fatal.
async function speakEleven(text, onErr) {
  if (!elevenConfigured) return "unavailable";
  const chunks = splitSentences(text);
  stopAllSpeech();
  const gen = elevenGen;
  let spokeAny = false;
  let pending = api.tts(chunks[0], savedElevenVoiceId);
  for (let i = 0; i < chunks.length; i++) {
    if (gen !== elevenGen) return spokeAny ? "spoke" : "unavailable";
    let blob;
    try {
      blob = await pending;
    } catch {
      if (!spokeAny) return "unavailable";
      pending = i + 1 < chunks.length ? api.tts(chunks[i + 1], savedElevenVoiceId) : null;
      continue;
    }
    if (gen !== elevenGen) return spokeAny ? "spoke" : "unavailable";
    pending = i + 1 < chunks.length ? api.tts(chunks[i + 1], savedElevenVoiceId) : null;
    const url = URL.createObjectURL(blob);
    const a = new Audio(url);
    elevenAudio = a;
    const ended = new Promise((resolve) => {
      a.onended = () => {
        URL.revokeObjectURL(url);
        if (elevenAudio === a) elevenAudio = null;
        resolve();
      };
      a.onerror = () => {
        URL.revokeObjectURL(url);
        resolve();
      };
    });
    try {
      await a.play();
    } catch (e) {
      URL.revokeObjectURL(url);
      // Autoplay policy: sound is blocked until the user has
      // interacted with the page. Say so plainly instead of going
      // quiet — the device-voice fallback would be blocked too.
      if (e?.name === "NotAllowedError" && !spokeAny) {
        onErr?.("Aries couldn't talk — the browser blocks sound until you tap something on the page. Tap anywhere, then ask again.");
        return "blocked";
      }
      return spokeAny ? "spoke" : "unavailable";
    }
    spokeAny = true;
    await ended;
  }
  return spokeAny ? "spoke" : "unavailable";
}

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

function speakBrowser(text, onErr) {
  if (!("speechSynthesis" in window)) {
    onErr?.("This browser has no built-in voices, and the ElevenLabs voice isn't set up — talk-back can't speak here.");
    return;
  }
  // Chrome drops a speak() that follows cancel() too closely, so the
  // reply would go silent with no error at all. Cancel, then speak a
  // beat later. The utterance's own error is reported, not swallowed.
  stopAllSpeech();
  const myGen = ++browserGen;
  const u = new SpeechSynthesisUtterance(text);
  const v = pickVoice();
  if (v) u.voice = v;
  u.rate = 1;
  u.pitch = 0.9;
  u.onerror = (e) => {
    if (e.error && e.error !== "canceled" && e.error !== "interrupted") {
      onErr?.(`Talk-back failed in this browser (${e.error}).`);
    }
  };
  setTimeout(() => {
    if (myGen !== browserGen) return; // superseded by a newer reply
    try { window.speechSynthesis.speak(u); } catch { /* ignore */ }
  }, 90);
}

// Talk-back: ElevenLabs narrator voice when the server has it,
// the device's built-in voices when it doesn't. onErr is called with
// a plain message when Aries can't speak at all, so the failure is
// visible instead of silent.
async function speak(text, on, onErr) {
  if (!on) return;
  const res = await speakEleven(text, onErr);
  if (res === "spoke" || res === "blocked") return;
  speakBrowser(text, onErr);
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
  const [moreTab, setMoreTab] = useState("aries");
  const [mini, setMini] = useState(false);
  const [talkBack, setTalkBack] = useState(true);
  const [wakeOn, setWakeOn] = useState(false);
  const [listening, setListening] = useState(false);
  const [permPrompt, setPermPrompt] = useState(null);
  const [due, setDue] = useState([]);
  const [locked, setLocked] = useState(false);
  const [setupOpen, setSetupOpen] = useState(false);
  const [notice, setNotice] = useState("");
  // Accounts: nobody sees chat before signing in. user is null until
  // /api/auth/me confirms the stored token; authReady says the check
  // has run, so the login screen never flashes over a signed-in app.
  const [user, setUser] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  // Desktop shell: a PC plan waiting for the user to confirm it.
  const [pcPlan, setPcPlan] = useState(null);
  // ElevenLabs voices from the server (empty until the key is set).
  const [eleven, setEleven] = useState({ configured: false, voices: [], defaultVoiceId: null, defaultVoiceName: "" });
  // Grain toggle swaps which baked grain image the veil shows.
  const [grain, setGrain] = useState("fine");
  // Burst button: each increment fires one burst from the veil's centre.
  const [burstSignal, setBurstSignal] = useState(0);
  const wakeRef = useRef(null);
  const inputRef = useRef(null);
  // Last "Hey Aries" trigger — one greeting per trigger, not one per
  // interim result. wakeOnRef mirrors the toggle for closures that
  // outlive their render (dictation resuming the wake listener).
  const lastWakeAt = useRef(0);
  const wakeOnRef = useRef(false);
  useEffect(() => { wakeOnRef.current = wakeOn; }, [wakeOn]);

  // Mini mode is the web front's body class: the visor shrinks, the
  // greeting and status fade out, and the stage moves to the bottom.
  // Pressing the face toggles it; Expand returns to full size.
  // In the desktop shell the window itself also shrinks to the
  // face-only mini window (and back), via the shell's IPC.
  useEffect(() => {
    document.body.classList.toggle("mini", mini);
    document.body.classList.toggle("desktop-mini", mini && isDesktop);
    if (isDesktop && window.ariesDesktop?.setMini) {
      window.ariesDesktop.setMini(mini).catch(() => {});
    }
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
      // 401 means the session died (logged out elsewhere, expired,
      // or the account was deleted) — back to the login screen.
      if (e.status === 401) {
        setToken(null);
        setUser(null);
        setData(null);
      } else {
        setError(e.message);
      }
    }
  }, []);

  // Sign-in check first: no token, or a dead token, means the login
  // screen. Only a confirmed account loads chat, voices and reminders.
  useEffect(() => {
    if (!getToken()) { setAuthReady(true); return; }
    api.me()
      .then((r) => {
        setUser(r.user);
        setAuthReady(true);
        load();
      })
      .catch((e) => {
        // 401 means the token is dead — back to login. Anything else
        // (server down, network) keeps the token and shows the error
        // screen instead, so a blip doesn't sign the user out.
        if (e.status === 401) setToken(null);
        else setError(e.message);
        setAuthReady(true);
      });
  }, [load]);

  useEffect(() => {
    if (!user) return;
    // ElevenLabs voices: success means the server has a voice key.
    // 503 means it doesn't — talk-back stays on the device's voices.
    api.voices()
      .then((v) => {
        elevenConfigured = true;
        setEleven({
          configured: true,
          voices: v.voices || [],
          defaultVoiceId: v.default_voice_id || null,
          defaultVoiceName: v.default_voice_name || "Adam",
        });
      })
      .catch(() => { elevenConfigured = false; });
    const t = setInterval(async () => {
      try {
        const d = await api.dueTasks();
        if (d.length) setDue(d);
      } catch { /* ignore */ }
    }, 30000);
    return () => clearInterval(t);
  }, [user]);

  async function handleLogout() {
    try { await api.logout(); } catch { /* token dies locally either way */ }
    stopAllSpeech();
    setToken(null);
    setUser(null);
    setData(null);
    setMessages([]);
    setShowMore(false);
    setLocked(false);
  }

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
    // Desktop shell: PC actions go to the local service (local LLM
    // plans the steps, the user confirms, then they run). Everything
    // else goes to the cloud chat, exactly like the web.
    if (isDesktop && looksLikePcCommand(msg)) {
      setFaceState("thinking");
      setStatus("THINKING");
      let plan;
      try {
        plan = await local.localChat(msg);
      } catch (e) {
        setFaceState("error");
        setStatus("ERROR");
        setMessages((m) => [...m, { role: "aries", content: `Something went wrong: ${e.message}` }]);
        return;
      }
      if (plan.needs_key) {
        setFaceState("idle");
        setStatus("READY");
        setMessages((m) => [...m, { role: "aries", content: plan.reply }]);
        setNotice("PC actions need the engine key — add it in Settings → More → Desktop.");
        return;
      }
      if (plan.needs_pc) {
        setFaceState("idle");
        setStatus("READY");
        setMessages((m) => [...m, { role: "aries", content: plan.reply }]);
        return;
      }
      if (plan.is_pc && plan.steps?.length) {
        // Show the plan and ask — nothing runs until the user says yes.
        // Destructive plans (shutdown, restart, delete) get a second
        // ask in the dialog itself.
        setPcPlan({
          msg,
          lines: plan.lines || [],
          steps: plan.steps,
          destructive: !!plan.destructive,
          warned: false,
        });
        setFaceState("idle");
        setStatus("READY");
        return;
      }
      // The local model answered in plain words — show it like a reply.
      setFaceState("speaking");
      setStatus("SPEAKING");
      setMessages((m) => [...m, { role: "aries", content: plan.reply || "Hey." }]);
      speak(plan.reply || "Hey.", talkBack, (m) => setNotice(m));
      setTimeout(() => { setFaceState("idle"); setStatus("READY"); }, 2500);
      return;
    }
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
      speak(res.reply, talkBack, (m) => setNotice(m));
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

  // PC plan confirmation (desktop shell only). Nothing runs until
  // the user confirms; a destructive plan asks a second time.
  async function confirmPcPlan() {
    const plan = pcPlan;
    if (!plan) return;
    if (plan.destructive && !plan.warned) {
      setPcPlan({ ...plan, warned: true });
      return;
    }
    setPcPlan(null);
    setFaceState("thinking");
    setStatus("THINKING");
    try {
      const res = await local.pcExecute(plan.steps);
      const text = res.done?.length
        ? `Done — ${res.done.join(" ")}`
        : "Done.";
      setMessages((m) => [...m, { role: "aries", content: text }]);
      speak(text, talkBack, (m) => setNotice(m));
    } catch (e) {
      setMessages((m) => [...m, { role: "aries", content: `Sorry — ${e.message}` }]);
    }
    setFaceState("idle");
    setStatus("READY");
  }
  function denyPcPlan() {
    setPcPlan(null);
    setMessages((m) => [...m, { role: "aries", content: "Okay — I didn't do anything." }]);
  }

  // The web wake-phrase listener. Its own function so the wake effect
  // and the dictation pause/resume can both start one. Fatal errors
  // (mic blocked, no mic) turn the toggle OFF and say so — the old
  // code swallowed them (onerror = () => {}) and restarted in a
  // silent loop. A 5-second guard stops one "Hey Aries" greeting
  // twice on the interim results.
  function startWakeRecognition() {
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
    let fatal = false;
    rec.onresult = (e) => {
      const said = Array.from(e.results).map((r) => r[0].transcript).join(" ").toLowerCase();
      if (said.includes("hey aries")) {
        const now = Date.now();
        if (now - lastWakeAt.current < 5000) return;
        lastWakeAt.current = now;
        setMini(false);
        const greet = "Aries active. Ready to cook.";
        setMessages((m) => [...m, { role: "aries", content: greet }]);
        speak(greet, true, (m) => setNotice(m));
        setTimeout(startDictation, 900);
      }
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        fatal = true;
        setNotice("Microphone is blocked, so the wake phrase can't listen. Allow the microphone for this site, then turn the wake phrase back on.");
        setWakeOn(false);
      } else if (e.error === "audio-capture") {
        fatal = true;
        setNotice("No microphone was found on this device, so the wake phrase is off.");
        setWakeOn(false);
      }
      // "no-speech" and "aborted" are routine — onend restarts below.
    };
    rec.onend = () => {
      if (!fatal && wakeRef.current === rec) {
        try { rec.start(); } catch { /* stopped */ }
      }
    };
    wakeRef.current = rec;
    try { rec.start(); } catch { /* ignore */ }
  }

  function startDictation() {
    // Desktop shell: dictation goes through the local service, whose
    // mic capture runs in a helper process — a bad audio driver can
    // never close the app. The service reports plainly when the mic
    // is unusable, and the app stays open.
    if (isDesktop) {
      if (listening) return;
      setListening(true);
      setFaceState("listening");
      setStatus("LISTENING");
      local.listen()
        .then((r) => {
          setListening(false);
          setFaceState("idle");
          setStatus("READY");
          if (r.heard && r.text) {
            setInput((v) => (v ? `${v} ${r.text}` : r.text));
            inputRef.current?.focus();
          } else {
            setNotice("Didn't catch that — try again, or type it.");
          }
        })
        .catch((e) => {
          setListening(false);
          setFaceState("idle");
          setStatus("READY");
          setNotice(`${e.message} Typing works as normal.`);
        });
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      setNotice("This browser has no speech recognition. Use your keyboard's mic key instead — it dictates straight into the box.");
      return;
    }
    // Don't start a second recognition while one is already running —
    // Chrome throws and the tap silently does nothing.
    if (listening) return;
    // The wake phrase holds a recognition open; starting dictation
    // while it listens throws "recognition already started" and both
    // go silent. Pause the wake listener and resume it when dictation
    // ends (once, whether it ended cleanly or with an error, and only
    // if the wake phrase is still switched on).
    const resumeWake = wakeOn && wakeRef.current;
    if (resumeWake) {
      try { wakeRef.current.stop(); } catch { /* ignore */ }
      wakeRef.current = null;
    }
    let wakeResumed = false;
    const maybeResumeWake = () => {
      if (resumeWake && !wakeResumed && wakeOnRef.current) {
        wakeResumed = true;
        startWakeRecognition();
      }
    };
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
      setFaceState("idle");
      setStatus("READY");
      if (e.error === "not-allowed") {
        setNotice("Microphone is blocked for this site. In Chrome: lock/tune icon → Site settings → Microphone → Allow, then reload. Or use your keyboard's mic key.");
      } else if (e.error === "no-speech") {
        setNotice("Didn't catch that — try again.");
      } else if (e.error === "aborted") {
        // Started over by something else — no need to alarm anyone.
      } else {
        setNotice(`Mic error: ${e.error}. Typing works as normal.`);
      }
      maybeResumeWake();
    };
    rec.onend = () => {
      setListening(false);
      setFaceState("idle");
      setStatus("READY");
      maybeResumeWake();
    };
    try {
      rec.start();
      setListening(true);
      setFaceState("listening");
      setStatus("LISTENING");
    } catch {
      setListening(false);
      setFaceState("idle");
      setStatus("READY");
      setNotice("The microphone wouldn't start — try again in a moment.");
      maybeResumeWake();
    }
  }

  useEffect(() => {
    if (!wakeOn) {
      wakeRef.current?.stop?.();
      wakeRef.current = null;
      if (isDesktop) local.wakeStop().catch(() => {});
      return;
    }
    // Desktop shell: the wake phrase is listened for by the local
    // service (mic capture in a helper process, so a bad driver can
    // never close the app). Poll for hearings once a second.
    if (isDesktop) {
      let stopped = false;
      local.wakeStart()
        .then(() => {
          const poll = async () => {
            if (stopped) return;
            try {
              const r = await local.wakePoll();
              if (r.wakes > 0) {
                setMini(false);
                const greet = "Aries active. Ready to cook.";
                setMessages((m) => [...m, { role: "aries", content: greet }]);
                speak(greet, true, (m) => setNotice(m));
                setTimeout(startDictation, 900);
              }
            } catch { /* service down — keep polling quietly */ }
            if (!stopped) wakeRef.current = setTimeout(poll, 1000);
          };
          wakeRef.current = setTimeout(poll, 1000);
        })
        .catch((e) => {
          setNotice(`${e.message} Wake phrase is off for now.`);
          setWakeOn(false);
        });
      return () => {
        stopped = true;
        clearTimeout(wakeRef.current);
        wakeRef.current = null;
        local.wakeStop().catch(() => {});
      };
    }
    startWakeRecognition();
    return () => {
      try { wakeRef.current?.stop?.(); } catch { /* ignore */ }
      wakeRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeOn]);

  async function dismissDue(task) {
    await api.markReminded(task.id).catch(() => {});
    setDue((d) => d.filter((t) => t.id !== task.id));
  }

  // Nobody sees chat before signing in — the login screen gates the
  // whole app. authReady says the token check has run, so a signed-in
  // user never sees the login screen flash by.
  if (!authReady) return <div className="aries-loading">ARIES — waking up…</div>;
  if (error && !data && !user) {
    return (
      <div className="aries-error">
        <h1>ARIES</h1>
        <p>Could not reach the Aries server: {error}</p>
        <p>Check the backend is running, then reload.</p>
      </div>
    );
  }
  if (!user) {
    return (
      <AuthScreen
        onAuthed={(u) => {
          setUser(u);
          load();
        }}
      />
    );
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
      <Veil grain={grain} burstSignal={burstSignal} />

      <div className="made-by" aria-hidden="true">© Made by Epsilon</div>

      <div className="topline">
        <span className="state-label">ARIES</span>
      </div>

      {!locked && (
        <button
          className="gear-btn"
          type="button"
          onClick={() => { setMoreTab("aries"); setShowMore(true); }}
          title="Settings"
          aria-label="Open settings"
        >⚙</button>
      )}

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
          {notice && <p className="notice" onClick={() => setNotice("")}>{notice} ✕</p>}
          <div className="composer-row">
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
        </div>
      )}

      {/* Mini mode: the face tap restores full size, and so does this
          button — a second, obvious way back, so mini can never trap
          the app again. */}
      {mini && !locked && (
        <button
          className="expand-btn"
          type="button"
          onClick={() => setMini(false)}
          aria-label="Return to full size"
        >
          ⤢ Full screen
        </button>
      )}

      {pcPlan && (
        <div className="perm-modal" role="dialog" aria-label="PC action confirmation">
          <div className="perm-card">
            <h2>{pcPlan.warned ? "Are you sure?" : "Aries wants to do this on your PC"}</h2>
            {pcPlan.warned ? (
              <p>This plan {pcPlan.destructive ? "shuts something down, restarts, or deletes" : "does something"} — it cannot be undone easily. Confirm a second time to run it.</p>
            ) : (
              <ul className="pc-plan">
                {pcPlan.lines.map((line, i) => <li key={i}>{line}</li>)}
              </ul>
            )}
            <div className="perm-actions">
              <button onClick={confirmPcPlan}>{pcPlan.warned ? "Yes, run it" : pcPlan.destructive ? "Continue…" : "Allow"}</button>
              <button onClick={denyPcPlan}>Don't run it</button>
            </div>
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
          user={user}
          onLogout={handleLogout}
          tab={moreTab}
          setTab={setMoreTab}
          onClose={() => { setShowMore(false); load(); }}
          useSearch={useSearch}
          setUseSearch={setUseSearch}
          talkBack={talkBack}
          setTalkBack={setTalkBack}
          wakeOn={wakeOn}
          setWakeOn={setWakeOn}
          grain={grain}
          setGrain={setGrain}
          onBurst={() => setBurstSignal((n) => n + 1)}
          elevenVoices={eleven.voices}
          elevenConfigured={eleven.configured}
          elevenDefaultId={eleven.defaultVoiceId}
          elevenDefaultName={eleven.defaultVoiceName}
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
  const [open, setOpen] = useState(false);
  return (
    <div className="sources">
      <button
        type="button"
        className="sources-btn"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        Sources ({sources.length}) {open ? "▴" : "▾"}
      </button>
      {open && (
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
      )}
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

// Let the user pick Aries' talk-back voice himself. When the server has
// an ElevenLabs key, the ElevenLabs voices come first — a real narrator
// voice (Adam by default) that speaks every reply. Below them, the
// voices on this device, which Aries uses only when ElevenLabs is off.
// Tap Hear to try one, tap Use this voice to make Aries speak with it.
// Both picks are saved per device in localStorage.
function VoicePicker({ elevenVoices, elevenConfigured, elevenDefaultId, elevenDefaultName }) {
  const [voices, setVoices] = useState([]);
  const [selected, setSelected] = useState(savedVoiceName);
  const [previewing, setPreviewing] = useState("");
  const [elevenSelected, setElevenSelected] = useState(savedElevenVoiceId);
  const [elevenPreviewing, setElevenPreviewing] = useState("");

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

  const previewEleven = async (voiceId) => {
    setElevenPreviewing(voiceId);
    try {
      const blob = await api.tts("Aries active. Ready to cook.", voiceId);
      const url = URL.createObjectURL(blob);
      stopAllSpeech();
      const a = new Audio(url);
      elevenAudio = a;
      a.onended = () => { URL.revokeObjectURL(url); setElevenPreviewing(""); };
      a.onerror = () => setElevenPreviewing("");
      await a.play();
    } catch { setElevenPreviewing(""); }
  };

  const chooseEleven = (id) => {
    setSavedElevenVoiceId(id);
    setElevenSelected(id);
  };

  const elevenNow = elevenSelected
    ? (elevenVoices || []).find((v) => v.voice_id === elevenSelected)?.name || "ElevenLabs voice"
    : `${elevenDefaultName || "Adam"} (default)`;

  return (
    <div className="voice-picker">
      <h3>Talk-back voice</h3>
      {elevenConfigured ? (
        <>
          <h4>ElevenLabs voices</h4>
          <p className="fine">A real narrator voice, served from the server. Tap Hear to try one, then Use this voice — this is the voice Aries speaks with.</p>
          <p>Now using: <strong>{elevenNow}</strong></p>
          <button onClick={() => chooseEleven(null)}>Use {elevenDefaultName || "Adam"} (default)</button>
          {(elevenVoices || []).length === 0 && <p className="fine">No ElevenLabs voices listed — the server key may not have voices yet.</p>}
          <ul className="voice-list">
            {(elevenVoices || []).map((v) => (
              <li key={v.voice_id} className={elevenSelected === v.voice_id ? "on" : ""}>
                <span>{v.name}{" "}
                  <span className="fine">
                    {v.labels?.gender ? `(${v.labels.gender}${v.labels?.accent ? `, ${v.labels.accent}` : ""})` : ""}
                  </span>
                </span>
                <span>
                  <button onClick={() => previewEleven(v.voice_id)}>{elevenPreviewing === v.voice_id ? "Playing…" : "Hear"}</button>
                  <button onClick={() => chooseEleven(v.voice_id)}>{elevenSelected === v.voice_id ? "✓ Using" : "Use this voice"}</button>
                </span>
              </li>
            ))}
          </ul>
          <h4>Voices on this device</h4>
          <p className="fine">Fallback only — used when the ElevenLabs voice is unavailable.</p>
        </>
      ) : (
        <p className="fine">The ElevenLabs voice is not set up on the server yet — add the key in Render and Aries will speak with a real narrator voice. Until then, these are the voices on this device.</p>
      )}
      <p className="fine">Tap Hear to try one, then tap Use this voice to make Aries speak with it. Your pick is saved on this device only.</p>
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
    </div>
  );
}

// Settings → Connections: link the accounts the skills need.
// Gmail and Spotify use real OAuth — Connect opens the provider's
// sign-in in a new tab; the server stores the tokens, and this UI only
// ever sees "connected" and the account name. WhatsApp has no one-tap
// sign-in for a personal account, so it is listed honestly with an
// explanation instead of a fake Connect button.
function ConnectionsSection() {
  const [conns, setConns] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setErr("");
      setConns(await api.connections());
    } catch (e) {
      setErr(e.message || "Could not load connections.");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function disconnect(service) {
    const label = service === "google" ? "Gmail" : "Spotify";
    if (!confirm(`Disconnect ${label}? Aries will no longer be able to use that account.`)) return;
    setBusy(true);
    try {
      await api.disconnectConnection(service);
      await load();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  }

  const rows = conns ? [
    { key: "google", desc: "Let Aries read and summarise your email.", ...conns.google },
    { key: "spotify", desc: "Let Aries play your music.", ...conns.spotify },
    { key: "whatsapp", desc: "Send and read your WhatsApp messages.", ...conns.whatsapp },
  ] : [];

  return (
    <section>
      <h3>Connections</h3>
      <p className="fine">Connect an account once and the skills that need it just work. Aries keeps the sign-in on the server — your password and tokens never appear here.</p>
      {err && <p className="notice">{err}</p>}
      {!conns && !err && <p className="fine">Loading…</p>}
      {rows.map((r) => (
        <div key={r.key} className="conn-row">
          <div className="conn-info">
            <strong>{r.name}</strong>
            <span className="conn-status">{r.connected ? `Connected${r.account ? ` — ${r.account}` : ""}` : "Not connected"}</span>
            <span className="fine">{r.desc}</span>
            {r.note && <span className="fine">{r.note}</span>}
            {!r.connected && !r.note && !r.configured && (
              <span className="fine">Not set up on the server yet — add the {r.key === "google" ? "Google" : "Spotify"} client in Render first (see below).</span>
            )}
          </div>
          <div className="conn-actions">
            {r.connected ? (
              <button disabled={busy} onClick={() => disconnect(r.key)}>Disconnect</button>
            ) : !r.note && (
              <button
                disabled={busy || !r.configured}
                onClick={() => window.open(api.connectUrl(r.key), "_blank", "noopener")}
              >
                Connect
              </button>
            )}
          </div>
        </div>
      ))}
      <button disabled={busy} onClick={load}>Check again</button>
      <p className="fine">Setting up Gmail or Spotify needs a free developer app first. In Google Cloud Console / Spotify Developer Dashboard, create an OAuth app and add these redirect URLs — {isDesktop ? "https://aries-ahvo.onrender.com" : window.location.origin}/api/connections/google/callback and {isDesktop ? "https://aries-ahvo.onrender.com" : window.location.origin}/api/connections/spotify/callback — then add the client ID and secret in Render (Environment) as GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET and SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET.</p>
    </section>
  );
}

// Desktop shell only (hidden on the web): the local service — PC
// control master toggle, the NVIDIA engine key (for local PC
// actions), and the service status. The key is saved to the OS
// credential store on this PC, never in code or in the cloud.
function DesktopSection() {
  const [pcOn, setPcOn] = useState(false);
  const [keyState, setKeyState] = useState({ has_key: false, source: null });
  const [svcOk, setSvcOk] = useState(null);
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    local.health().then(() => setSvcOk(true)).catch(() => setSvcOk(false));
    local.pcEnabled().then((r) => setPcOn(r.enabled)).catch(() => {});
    local.keyStatus().then(setKeyState).catch(() => {});
  }, []);

  async function togglePc() {
    const next = !pcOn;
    try {
      await local.setPcEnabled(next);
      setPcOn(next);
      setMsg(next ? "PC control is on. Aries will ask before acting." : "PC control is off.");
    } catch (e) { setMsg(e.message); }
  }
  async function saveKey() {
    if (!key.trim()) { setMsg("Paste a key first."); return; }
    try {
      await local.saveKey(key.trim());
      setKey("");
      setKeyState(await local.keyStatus());
      setMsg("Engine key saved on this PC.");
    } catch (e) { setMsg(e.message); }
  }
  return (
    <section>
      <h3>Desktop</h3>
      <p className="fine">Local service: {svcOk === null ? "checking…" : svcOk ? "running" : "not running — PC control, mic and wake phrase need it. Restart the app."}</p>
      <div className="toggle-row">
        <button className="pill" type="button" aria-pressed={pcOn} onClick={togglePc}>
          PC control {pcOn ? "(on)" : "(off)"}
        </button>
      </div>
      <p className="fine">PC control is off by default. When on, Aries asks before acting on your PC — and asks a second time before anything destructive (shutdown, restart, delete).</p>
      <label>Engine key (NVIDIA, for PC actions)
        <input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="nvapi-…" />
      </label>
      <p className="fine">Status: {keyState.has_key ? `saved (${keyState.source})` : "no key on this PC yet"}. The key is saved to the Windows Credential Manager, never in code.</p>
      <button onClick={saveKey}>Save engine key</button>
      {msg && <p className="notice">{msg}</p>}
    </section>
  );
}

// Sign-in / sign-up gate: the whole app sits behind this until the
// server confirms a token. Each account gets only its own data —
// Epsilon signs in as "Epsilon" (the admin account, by username);
// Kelfy makes her own account and sees only hers.
function AuthScreen({ onAuthed }) {
  const [mode, setMode] = useState("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function submit(e) {
    e.preventDefault();
    if (!username.trim() || !password) { setErr("Enter a username and a password."); return; }
    setErr("");
    setBusy(true);
    try {
      const r = mode === "login"
        ? await api.login(username.trim(), password)
        : await api.register(username.trim(), password);
      setToken(r.token);
      onAuthed(r.user);
    } catch (e2) {
      setErr(e2.message);
    }
    setBusy(false);
  }
  return (
    <div className="auth-screen">
      <Face state="idle" label="Aries" />
      <h1>ARIES</h1>
      <p className="auth-sub">Sign in to your own Aries. Your chats, memories and tasks are yours alone.</p>
      <form onSubmit={submit}>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Username"
          autoComplete="username"
          aria-label="Username"
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          aria-label="Password"
        />
        {err && <p className="notice">{err}</p>}
        <button className="auth-btn" type="submit" disabled={busy}>
          {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
      </form>
      <button
        type="button"
        className="link-btn"
        onClick={() => { setMode(mode === "login" ? "signup" : "login"); setErr(""); }}
      >
        {mode === "login" ? "No account yet? Create one" : "Already have an account? Sign in"}
      </button>
      <p className="fine">Epsilon signs in as “Epsilon” — that account is the admin, no matter when it was created.</p>
    </div>
  );
}

// Admin panel (Settings → More): only the account holding the admin
// username sees this. Lists every account; deleting one removes the
// account and all of its chats, memories, tasks and profile. The
// admin account itself can't be deleted from here.
function AdminPanel() {
  const [users, setUsers] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const loadUsers = useCallback(async () => {
    try {
      setErr("");
      setUsers((await api.adminUsers()).users || []);
    } catch (e) {
      setErr(e.message || "Could not load users.");
    }
  }, []);
  useEffect(() => { loadUsers(); }, [loadUsers]);
  async function remove(username) {
    if (!confirm(`Delete ${username}'s account? Their chats, memories and tasks go with it. This can't be undone.`)) return;
    setBusy(true);
    try {
      await api.adminDeleteUser(username);
      await loadUsers();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  }
  return (
    <section>
      <h3>Admin — accounts</h3>
      <p className="fine">Everyone gets their own account and their own data. Only “Epsilon” is admin — by username, not by who signed up first.</p>
      {err && <p className="notice">{err}</p>}
      {!users && !err && <p className="fine">Loading…</p>}
      <ul className="user-list">
        {(users || []).map((u) => (
          <li key={u.username}>
            <span><strong>{u.username}</strong> — {u.role}{u.created_at ? ` · since ${new Date(u.created_at).toLocaleDateString()}` : ""}</span>
            {u.role !== "admin" && (
              <button disabled={busy} onClick={() => remove(u.username)}>Delete</button>
            )}
          </li>
        ))}
      </ul>
      <button disabled={busy} onClick={loadUsers}>Refresh</button>
    </section>
  );
}

// Voice self-test (Settings → Aries): checks each voice path one by
// one and says plainly what works and what doesn't, instead of
// leaving the user guessing why the mic or talk-back is quiet. The
// microphone test asks the browser for the mic — that prompt is the
// test, and the stream is stopped straight away.
function VoiceSelfTest({ elevenConfigured, elevenDefaultName }) {
  const [results, setResults] = useState(null);
  const [running, setRunning] = useState(false);

  async function run() {
    setRunning(true);
    const r = {};
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    r.recognition = SR
      ? { ok: true, text: "Speech recognition is available in this browser — dictation and the wake phrase can work." }
      : { ok: false, text: "No speech recognition in this browser — dictation and the wake phrase can't work here. Chrome on Android or desktop works; the Muse preview frame does not." };
    try {
      if (navigator.permissions?.query) {
        const st = await navigator.permissions.query({ name: "microphone" });
        r.mic = st.state === "granted"
          ? { ok: true, text: "Microphone permission: granted." }
          : st.state === "denied"
            ? { ok: false, text: "Microphone permission: blocked for this site. Allow it in the browser's site settings, then test again." }
            : { ok: null, text: "Microphone permission: not decided yet — the mic test below will ask." };
      } else {
        r.mic = { ok: null, text: "This browser doesn't report microphone permission — the mic test below will tell you." };
      }
    } catch {
      r.mic = { ok: null, text: "Microphone permission couldn't be checked in this browser." };
    }
    try {
      if (navigator.mediaDevices?.getUserMedia) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
        r.micLive = { ok: true, text: "Microphone test: the mic opened fine — Aries can hear you." };
      } else {
        r.micLive = { ok: false, text: "Microphone test: this browser gives no microphone access." };
      }
    } catch (e) {
      r.micLive = {
        ok: false,
        text: e?.name === "NotAllowedError"
          ? "Microphone test: blocked. Allow the microphone for this site in the browser's site settings, then test again."
          : `Microphone test failed: ${e?.name || "unknown error"}.`,
      };
    }
    try {
      const vs = ("speechSynthesis" in window) ? window.speechSynthesis.getVoices() || [] : [];
      const en = vs.filter((v) => /^en/i.test(v.lang || ""));
      r.voices = vs.length
        ? { ok: true, text: `Talk-back voices: ${en.length} English voice(s) on this device (${vs.length} total).` }
        : { ok: false, text: "Talk-back voices: none loaded yet — close Settings and reopen it; voices load a moment after the page opens." };
    } catch {
      r.voices = { ok: false, text: "Talk-back voices: speech synthesis isn't available in this browser." };
    }
    r.eleven = elevenConfigured
      ? { ok: true, text: `ElevenLabs voice: set up — Aries talks back with ${elevenDefaultName || "Adam"}.` }
      : { ok: false, text: "ElevenLabs voice: not set up on the server — talk-back uses the device's voices." };
    setResults(r);
    setRunning(false);
  }

  const rows = results ? [
    ["Speech recognition", results.recognition],
    ["Microphone permission", results.mic],
    ["Microphone test", results.micLive],
    ["Talk-back voices", results.voices],
    ["ElevenLabs voice", results.eleven],
  ] : [];

  return (
    <div className="voice-test">
      <h3>Voice self-test</h3>
      <p className="fine">Checks each voice path and says plainly what works. The microphone test asks the browser for the mic — that prompt is the test.</p>
      <button disabled={running} onClick={run}>{running ? "Testing…" : "Run voice test"}</button>
      {rows.map(([label, res]) => (
        <p key={label} className="test-row">
          <span className={res.ok === true ? "ok" : res.ok === false ? "bad" : ""}>
            {res.ok === true ? "✓ " : res.ok === false ? "✗ " : "• "}
          </span>
          <span><strong>{label}:</strong> {res.text}</span>
        </p>
      ))}
    </div>
  );
}

function MoreSheet({ data, user, onLogout, tab, setTab, onClose, useSearch, setUseSearch, talkBack, setTalkBack, wakeOn, setWakeOn, grain, setGrain, onBurst, elevenVoices, elevenConfigured, elevenDefaultId, elevenDefaultName }) {
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

  const tabs = ["aries", "memory", "more", "connections"];
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

      {tab === "aries" && (
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
          <VoicePicker
            elevenVoices={elevenVoices}
            elevenConfigured={elevenConfigured}
            elevenDefaultId={elevenDefaultId}
            elevenDefaultName={elevenDefaultName}
          />
          <h3>Talk &amp; search</h3>
          <div className="toggle-row">
            <button className="pill" type="button" aria-pressed={talkBack} onClick={() => setTalkBack((v) => !v)}>
              Talk back
            </button>
            <button className="pill" type="button" aria-pressed={wakeOn} onClick={() => setWakeOn((v) => !v)}>
              Wake phrase (“Hey Aries”)
            </button>
            <button className="pill" type="button" aria-pressed={useSearch} onClick={() => setUseSearch((v) => !v)}>
              Search the web
            </button>
          </div>
          <p className="fine">Talk back reads Aries' replies aloud. Wake phrase listens for “Hey Aries”. Search the web grounds the next answer in live results.</p>
          <VoiceSelfTest elevenConfigured={elevenConfigured} elevenDefaultName={elevenDefaultName} />
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
          <h3>About you — from Aries' memory file</h3>
          <p className="fine">Durable facts Aries always knows, kept in its memory file on the server. This is read-only — day-to-day things Aries learns live in the vault above.</p>
          {data.memory_file ? (
            <div className="memory-file">{data.memory_file}</div>
          ) : (
            <p className="fine">No memory file on the server yet.</p>
          )}
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

      {tab === "more" && (
        <>
        <section>
          <h3>Account</h3>
          <p>Signed in as <strong>{user?.username}</strong>{user?.role === "admin" ? " — admin" : ""}. Your chats, memories and tasks are yours alone.</p>
          <button onClick={onLogout}>Log out</button>
        </section>
        {user?.role === "admin" && <AdminPanel />}
        <section>
          <h3>Veil</h3>
          <p className="fine">The veil is the dithered grain over the mountains. Tap the veil itself for a burst, or use these.</p>
          <div className="grain-group" role="group" aria-label="Veil grain">
            <button className="pill" type="button" aria-pressed={grain === "fine"} onClick={() => setGrain("fine")}>
              Fine grain
            </button>
            <button className="pill" type="button" aria-pressed={grain === "coarse"} onClick={() => setGrain("coarse")}>
              Coarse grain
            </button>
          </div>
          <button className="pill" type="button" onClick={onBurst}>
            Release a burst
          </button>
        </section>
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
          <p className="fine">Tip: in chat, say “remind me to call Kelfy tomorrow at 9am” and Aries saves it.</p>
        </section>

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

        <section>
          <h3>Face login</h3>
          <p>Status: {data.face?.enrolled ? "Enrolled" : "Not enrolled"}</p>
          <input type="file" accept="image/*" onChange={(e) => setFaceFile(e.target.files[0])} />
          <input type="password" value={facePin} onChange={(e) => setFacePin(e.target.value)} placeholder="Unlock PIN (optional, sets fallback)" />
          <button onClick={enroll}>Enroll my face</button>
          {data.face?.enrolled && <button onClick={() => { if (confirm("Remove face enrollment?")) api.removeFace().then(onClose); }}>Remove enrollment</button>}
          <p className="fine">OpenCV LBPH recognition — a convenience lock, not biometric-grade security. The PIN is the real fallback; only its hash is stored.</p>
        </section>

        <section>
          <h3>Screenshot</h3>
          <p className="fine">Aries asks permission before reading any image.</p>
          <input type="file" accept="image/*" onChange={(e) => setShotFile(e.target.files[0])} />
          <input value={shotQ} onChange={(e) => setShotQ(e.target.value)} placeholder="Ask about the image (optional)" />
          <button onClick={describeShot}>Describe this image</button>
          {shotDesc && <div className="shot-desc"><p>{shotDesc}</p></div>}
        </section>

        <section>
          <h3>Desktop app &amp; installer</h3>
          <p>The desktop app (0.7.0) is the same React app as this website,
            bundled locally into a real installed PC program — never the
            live site in a window. It fills the full screen when activated,
            is freely resizable, and tapping the bot drops it into mini mode
            floating bottom-right, just above the taskbar. A local service
            on your PC handles PC control, the microphone and the wake
            phrase; chat, search, memory and voice still come from the
            server.</p>
          <p><strong>Engine key:</strong> server-side only. The installer never contains
            an API key — users sign in to the SaaS backend instead. Aries is private
            to Epsilon and Kelfy until it goes commercial.</p>
          <p><strong>Roadmap:</strong> web search ✓ · voice ✓ · tasks ✓ · installer ✓ ·
            face login ✓ · screenshot ✓ · PC control ✓ (in the desktop app).</p>
          <a className="download-btn" href="https://github.com/Epsilon-Odinson/Aries/releases/download/v0.7.0-desktop/Aries-Setup.exe">Download for Windows (Installer)</a>
          <p style={{ marginTop: "8px" }}>The installer sets up Aries, offers a desktop shortcut and a taskbar pin. Prefer the portable single file instead? Get <a href="https://github.com/Epsilon-Odinson/Aries/releases/download/v0.7.0-desktop/Aries.exe">Aries.exe</a> from the same release.</p>
        </section>

        {isDesktop && <DesktopSection />}
        </>
      )}

      {tab === "connections" && <ConnectionsSection />}
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
            <p>Permission defaults — change any of these later in Settings → More.</p>
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
