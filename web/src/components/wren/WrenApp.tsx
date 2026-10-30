import { Component, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { IllList, IllListen, IllPrivacy, IllRecord } from "./illustrations";

type Item = { id: string; day: number; sec: string; text: string; hint: string; done: boolean; isNew: boolean };

type AppKey = "gmail" | "cal" | "wa" | "slack";
type ChatMessage = { id: string; from: "me" | "wren"; text: string };
type State = {
  mode: "onb" | "list" | "chat" | "apps";
  onbStep: number;
  recording: boolean;
  saving: boolean;
  elapsed: number;
  banner: string;
  day: number;
  hold: number;
  tapHint: boolean;
  items: Item[];
  draft: string;
  messages: ChatMessage[];
  apps: Record<AppKey, boolean>;
  asking: boolean;
  hearing: boolean;
};

const C = { ink: "#111111", white: "#FFFFFF", muted: "#6B6B6B", faint: "#767676", line: "#E3E3E3", soft: "#F2F2F2", green: "#14B86E", greenDark: "#0B7A48" };

const SECTIONS = ["Reminders", "To-do", "Promises", "Plans", "Ideas", "Talked about"];
const CARRY: Record<string, boolean> = { Reminders: true, "To-do": true, Promises: true, Plans: true };
const SECTION_BY_TAG: Record<string, string> = {
  reminder: "Reminders",
  reminders: "Reminders",
  todo: "To-do",
  "to-do": "To-do",
  "to do": "To-do",
  promise: "Promises",
  promises: "Promises",
  plan: "Plans",
  plans: "Plans",
  idea: "Ideas",
  ideas: "Ideas",
  "talked about": "Talked about",
};

const ONB = [
  { title: "Wren remembers for you.", body: "It listens to your day and writes down what matters, so you don’t have to." },
  { title: "Tap Record. Then live your day.", body: "Talk like you always do. When you’re done, hold the button to stop." },
  { title: "Your list makes itself.", body: "To-dos, promises and plans go in the right place. Wren reminds you right when it matters." },
  { title: "Wren needs your microphone.", body: "" },
];

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const DONE_KEY = "wren-done";
const APPS_KEY = "wren-apps";
const CONNECT_APPS: { key: AppKey; name: string; icon: "mail" | "cal" | "chat" | "work"; desc: string }[] = [
  { key: "gmail", name: "Gmail", icon: "mail", desc: "Find promises and to-dos in your email." },
  { key: "cal", name: "Google Calendar", icon: "cal", desc: "Put your plans in your calendar." },
  { key: "wa", name: "WhatsApp", icon: "chat", desc: "Get your reminders as a WhatsApp message." },
  { key: "slack", name: "Slack", icon: "work", desc: "Catch tasks from work chats." },
];
const CHIPS = ["What’s on today?", "What did I promise?", "Remind me to call Mom tonight", "What’s Kabir allergic to?"];
const EMPTY_APPS: Record<AppKey, boolean> = { gmail: false, cal: false, wa: false, slack: false };

const pad = (n: number) => (n < 10 ? "0" + n : String(n));

const svgBase = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
const Chevron = ({ dir }: { dir: "l" | "r" }) => (
  <svg width="20" height="20" {...svgBase} strokeWidth="2.2"><polyline points={dir === "l" ? "15 5 8 12 15 19" : "9 5 16 12 9 19"} /></svg>
);
const Mic = () => (
  <svg width="20" height="20" {...svgBase} strokeWidth="2"><rect x="9" y="3.5" width="6" height="11" rx="3" /><path d="M6 11a6 6 0 0 0 12 0" /><line x1="12" y1="17" x2="12" y2="20.5" /></svg>
);
const Check = ({ size = 14, w = 3.2 }: { size?: number; w?: number }) => (
  <svg width={size} height={size} {...svgBase} strokeWidth={w}><polyline points="5 12 10 17 19 7" /></svg>
);

const circleBtn: CSSProperties = { width: 44, height: 44, flexShrink: 0, borderRadius: "50%", border: `1.5px solid ${C.line}`, background: "transparent", color: C.ink, display: "flex", alignItems: "center", justifyContent: "center", padding: 0, cursor: "pointer" };
const dot = (size: number, extra?: CSSProperties): CSSProperties => ({ width: size, height: size, borderRadius: "50%", background: C.green, display: "block", flexShrink: 0, ...extra });

function recentDays() {
  return [0, -1, -2, -3].map((offset) => {
    const date = new Date();
    date.setHours(12, 0, 0, 0);
    date.setDate(date.getDate() + offset);
    const weekday = WEEKDAYS[date.getDay()] || "";
    const title = offset === 0 ? "Today" : offset === -1 ? "Yesterday" : weekday;
    const label = date.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
    return { offset, title, date: label, from: offset === 0 ? "" : `From ${weekday} · ` };
  });
}

function dayOffset(iso: string | undefined) {
  if (!iso) return 0;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return 0;
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((start(new Date()) - start(then)) / 86400000);
  return days > 0 ? -Math.min(days, 3) : 0;
}

function readDone() {
  try {
    const parsed = JSON.parse(localStorage.getItem(DONE_KEY) || "[]");
    return new Set<string>(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set<string>();
  }
}

function writeDone(ids: Set<string>) {
  localStorage.setItem(DONE_KEY, JSON.stringify([...ids]));
}

function pointsFromSummary(sessionId: string, summary: string, when: string | undefined, isNew: boolean): Item[] {
  const done = readDone();
  const day = dayOffset(when);
  return summary
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("-") || line.startsWith("•"))
    .map((line, index) => {
      let text = line.replace(/^[-•]\s*/, "");
      const tag = text.match(/\[([^\]]+)\]\s*$/);
      let sec = "Talked about";
      if (tag?.[1]) {
        sec = SECTION_BY_TAG[tag[1].trim().toLowerCase()] || sec;
        text = text.slice(0, tag.index).trim().replace(/[;.]\s*$/, "");
      }
      const id = `${sessionId}-${index}`;
      return { id, day, sec, text, hint: "From this recording", done: done.has(id), isNew };
    })
    .filter((item) => item.text.length > 0);
}

type SessionRow = { session_id?: string; summary?: string; updated_at?: string; summary_updated_at?: string };

export class WrenApp extends Component<object, State> {
  _timers: ReturnType<typeof setTimeout>[] = [];
  _tick?: ReturnType<typeof setInterval>;
  _hold: ReturnType<typeof setInterval> | null = null;
  _hintId = 0;
  _bannerId = 0;
  mediaStream: MediaStream | null = null;
  mediaRecorder: MediaRecorder | null = null;
  sliceTimer: number | null = null;
  uploadChain: Promise<void> = Promise.resolve();
  chunkIndex = 0;
  sessionId = "";
  listening = false;
  voiceStream: MediaStream | null = null;
  voiceRecorder: MediaRecorder | null = null;

  constructor(props: object) {
    super(props);
    let apps = EMPTY_APPS;
    try {
      const stored = JSON.parse(localStorage.getItem(APPS_KEY) || "");
      if (stored && typeof stored === "object") apps = { ...EMPTY_APPS, ...stored };
    } catch { /* keep every app off */ }
    this.state = {
      mode: "onb", onbStep: 0, recording: false, saving: false, elapsed: 0, banner: "", day: 0, hold: 0, tapHint: false,
      items: [], draft: "", messages: [], apps, asking: false, hearing: false,
    };
  }

  override componentDidMount() {
    this._tick = setInterval(() => {
      if (this.listening) this.setState((s) => ({ elapsed: s.elapsed + 1 }));
    }, 1000);
    void this.loadItems(false);
  }

  async sendChat(text: string) {
    const clean = text.trim();
    if (!clean || this.state.asking) return;
    const id = String(Date.now());
    this.setState((st) => ({ asking: true, draft: "", messages: st.messages.concat([{ id, from: "me", text: clean }]) }));
    await this.finishAnswer(id, clean);
  }

  async finishAnswer(id: string, text: string, spoken?: { file: Blob; name: string }) {
    try {
      let answer = "";
      if (spoken) {
        const form = new FormData();
        form.append("file", spoken.file, spoken.name);
        const res = await fetch("/api/chat/voice", { method: "POST", body: form });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "I could not hear that.");
        answer = (data.answer || "").trim() || "I have not heard that yet.";
      } else {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "I could not look through your notes.");
        answer = (data.answer || "").trim() || "I have not heard that yet.";
      }
      this.setState((st) => ({ asking: false, messages: st.messages.concat([{ id: id + "w", from: "wren", text: answer }]) }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "I could not look through your notes.";
      this.setState((st) => ({ asking: false, messages: st.messages.concat([{ id: id + "w", from: "wren", text: message }]) }));
    }
  }

  async askByVoice() {
    if (this.state.asking) return;
    if (this.voiceRecorder && this.voiceRecorder.state === "recording") {
      this.voiceRecorder.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.voiceStream = stream;
      const mime = this.recorderMime();
      const rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      this.voiceRecorder = rec;
      const parts: Blob[] = [];
      rec.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) parts.push(event.data);
      };
      rec.onstop = () => {
        this.voiceStream?.getTracks().forEach((track) => track.stop());
        this.voiceStream = null;
        this.voiceRecorder = null;
        const blob = new Blob(parts, { type: rec.mimeType || "audio/webm" });
        const id = String(Date.now());
        this.setState({ hearing: false, asking: true });
        void this.finishAnswer(id, "", { file: blob, name: "question.webm" });
      };
      rec.start();
      this.setState({ hearing: true });
    } catch {
      this.setState({ hearing: false, asking: false, banner: "Microphone is blocked. Allow it in the browser, then try again." });
    }
  }

  toggleApp(key: AppKey) {
    this.setState((st) => {
      const apps = { ...st.apps, [key]: !st.apps[key] };
      localStorage.setItem(APPS_KEY, JSON.stringify(apps));
      return { apps };
    });
  }

  override componentWillUnmount() {
    if (this._tick) clearInterval(this._tick);
    if (this._hold) clearInterval(this._hold);
    if (this.sliceTimer) window.clearTimeout(this.sliceTimer);
    this.releaseMic();
    this._timers.forEach(clearTimeout);
  }

  later(fn: () => void, ms: number) { this._timers.push(setTimeout(fn, ms)); }

  async loadItems(markNewSession: string | null) {
    try {
      const res = await fetch("/api/sessions");
      if (!res.ok) return [] as Item[];
      const rows = (await res.json()) as SessionRow[];
      const items = rows.flatMap((row) => {
        if (!row.session_id || !row.summary) return [];
        return pointsFromSummary(
          row.session_id,
          row.summary,
          row.summary_updated_at || row.updated_at,
          markNewSession === row.session_id,
        );
      });
      this.setState({ items });
      return items;
    } catch {
      return this.state.items;
    }
  }

  finishOnb(allowed: boolean) {
    this.setState({ mode: "list", onbStep: 0, banner: "" });
    if (allowed) {
      navigator.mediaDevices?.getUserMedia({ audio: true }).then((stream) => {
        stream.getTracks().forEach((track) => track.stop());
      }).catch(() => {
        this.setState({ banner: "Microphone is blocked. Allow it in the browser, then tap Record." });
      });
    }
  }

  showBanner(text: string) {
    this.setState({ banner: text });
    const id = ++this._bannerId;
    this.later(() => { if (this._bannerId === id) this.setState({ banner: "" }); }, 4000);
  }

  toggleDone(id: string) {
    const done = readDone();
    if (done.has(id)) done.delete(id);
    else done.add(id);
    writeDone(done);
    this.setState((p) => ({ items: p.items.map((item) => (item.id === id ? { ...item, done: !item.done } : item)) }));
  }

  async start() {
    if (this.listening || this.state.saving) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.mediaStream = stream;
      this.sessionId = "web_" + Date.now();
      this.chunkIndex = 0;
      this.uploadChain = Promise.resolve();
      this.listening = true;
      this.setState((st) => ({
        recording: true, saving: false, elapsed: 0, banner: "", hold: 0, tapHint: false,
        items: st.items.map((item) => (item.isNew ? { ...item, isNew: false } : item)),
      }));
      this.startSlice();
    } catch {
      this.setState({ mode: "list", banner: "Microphone is blocked. Allow it in the browser, then tap Record." });
    }
  }

  recorderMime() {
    if (typeof MediaRecorder === "undefined") return "";
    if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) return "audio/webm;codecs=opus";
    if (MediaRecorder.isTypeSupported("audio/webm")) return "audio/webm";
    return "";
  }

  startSlice() {
    if (!this.listening || !this.mediaStream) return;
    const mime = this.recorderMime();
    const rec = mime ? new MediaRecorder(this.mediaStream, { mimeType: mime }) : new MediaRecorder(this.mediaStream);
    this.mediaRecorder = rec;
    const parts: Blob[] = [];
    rec.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) parts.push(event.data);
    };
    rec.onstop = () => {
      const blob = new Blob(parts, { type: rec.mimeType || "audio/webm" });
      const last = !this.listening;
      if (!last) this.startSlice();
      else this.releaseMic();
      if (blob.size > 200) this.enqueueChunk(blob, last);
      else if (last) void this.finishRecording(null, "That stretch was too short to transcribe.");
    };
    rec.start();
    this.sliceTimer = window.setTimeout(() => {
      if (rec.state === "recording") rec.stop();
    }, 5000);
  }

  releaseMic() {
    this.mediaStream?.getTracks().forEach((track) => track.stop());
    this.mediaStream = null;
  }

  enqueueChunk(blob: Blob, isLast: boolean) {
    const index = this.chunkIndex++;
    const sessionId = this.sessionId;
    this.uploadChain = this.uploadChain.then(() => this.sendChunk(blob, index, isLast, sessionId));
  }

  async sendChunk(blob: Blob, index: number, isLast: boolean, sessionId: string) {
    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("chunk_index", String(index));
    form.append("is_last", isLast ? "true" : "false");
    form.append("file", blob, `chunk_${index}.webm`);
    try {
      const res = await fetch("/api/transcribe/chunk", { method: "POST", body: form });
      if (!res.ok) {
        let detail = "The recording could not be transcribed.";
        try {
          const err = await res.json() as { detail?: string };
          if (err.detail) detail = err.detail;
        } catch { /* keep the fallback */ }
        if (isLast) await this.finishRecording(sessionId, detail);
        return;
      }
      const data = await res.json() as { summary?: string; summary_error?: string };
      if (isLast) await this.finishRecording(sessionId, data.summary_error || "");
    } catch {
      if (isLast) await this.finishRecording(sessionId, "Could not reach the recorder. Start the API and try again.");
    }
  }

  async finishRecording(sessionId: string | null, error: string) {
    this.listening = false;
    const items = await this.loadItems(sessionId);
    const fresh = sessionId ? items.filter((item) => item.id.startsWith(sessionId + "-")).length : 0;
    const banner = error
      ? error
      : fresh === 0
        ? "Nothing new this time."
        : "Added " + fresh + (fresh === 1 ? " new thing." : " new things.");
    this.setState({ recording: false, saving: false, mode: "list", day: 0, hold: 0, tapHint: false });
    this.showBanner(banner);
  }

  holdStart(event?: { preventDefault?: () => void }) {
    event?.preventDefault?.();
    if (this._hold || !this.state.recording || this.state.saving) return;
    this.setState({ tapHint: false });
    this._hold = setInterval(() => {
      const next = this.state.hold + 3.5;
      if (next >= 100) {
        if (this._hold) clearInterval(this._hold);
        this._hold = null;
        this.setState({ hold: 100, saving: true });
        this.later(() => this.stop(), 120);
      } else this.setState({ hold: next });
    }, 35);
  }

  holdEnd() {
    if (!this._hold) return;
    clearInterval(this._hold);
    this._hold = null;
    this.setState({ hold: 0, tapHint: true });
    const id = ++this._hintId;
    this.later(() => { if (this._hintId === id) this.setState({ tapHint: false }); }, 2600);
  }

  stop() {
    if (!this.listening) return;
    this.listening = false;
    if (this.sliceTimer) window.clearTimeout(this.sliceTimer);
    this.setState({ saving: true, hold: 100, tapHint: false });
    if (this.mediaRecorder && this.mediaRecorder.state === "recording") this.mediaRecorder.stop();
    else void this.finishRecording(this.sessionId, "That stretch was too short to transcribe.");
  }

  renderOnb() {
    const step = this.state.onbStep;
    const screen = ONB[step]!;
    const last = step === 3;
    const Ill = [IllListen, IllRecord, IllList, IllPrivacy][step]!;
    const li: CSSProperties = { display: "flex", gap: 12, alignItems: "flex-start", fontSize: 17, lineHeight: 1.4 };
    const liIcon = { width: 22, height: 22, ...svgBase, stroke: C.ink, strokeWidth: 2, style: { flexShrink: 0, marginTop: 1 } };
    return (
      <>
        <div style={{ flexShrink: 0, height: 44, padding: "54px 24px 0", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div role="img" aria-label={`Step ${step + 1} of 4`} style={{ display: "flex", gap: 6 }}>
            {ONB.map((_, i) => (
              <span key={i} style={{ width: i === step ? 24 : 8, height: 8, borderRadius: 4, background: i <= step ? C.ink : "#D4D4D4", display: "block", transition: "width .25s ease, background .25s ease" }} />
            ))}
          </div>
          {step < 3 && (
            <button onClick={() => this.setState({ onbStep: 3 })} style={{ height: 44, padding: "0 12px", marginRight: -12, border: 0, background: "transparent", color: C.muted, fontSize: 16, fontWeight: 600, cursor: "pointer" }}>Skip</button>
          )}
        </div>
        <div className="v3-scroll" style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto", display: "flex", flexDirection: "column", alignItems: "center", paddingTop: last ? 12 : 28 }}>
          <div className="v3-in" key={"ill" + step}><Ill /></div>
          <div className="v3-in" key={"txt" + step} style={{ alignSelf: "stretch", padding: `${last ? 20 : 32}px 28px 8px`, display: "flex", flexDirection: "column", gap: 14 }}>
            <h1 style={{ margin: 0, fontSize: 32, fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1.12 }}>{screen.title}</h1>
            {screen.body && <p style={{ margin: 0, fontSize: 18, lineHeight: 1.5, color: C.muted }}>{screen.body}</p>}
            {last && (
              <ul style={{ margin: "4px 0 0", padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 14 }}>
                <li style={li}><svg {...liIcon}><circle cx="12" cy="12" r="8.5" /><polyline points="12 7.5 12 12 15 14" /></svg><span>Recordings are deleted after 24 hours. Only your notes stay.</span></li>
                <li style={li}><svg {...liIcon}><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg><span>Nothing is shared without asking you.</span></li>
                <li style={li}><svg {...liIcon}><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0" /><path d="M16 5.5a3 3 0 0 1 0 5.5" /><path d="M17.5 14.5a5 5 0 0 1 3 4.5" /></svg><span>Let people near you know Wren is listening.</span></li>
              </ul>
            )}
          </div>
        </div>
        <div style={{ flexShrink: 0, padding: `12px 24px ${last ? 26 : 34}px`, display: "flex", flexDirection: "column", gap: 4 }}>
          <button className="v3-press" onClick={() => (step < 3 ? this.setState({ onbStep: step + 1 }) : this.finishOnb(true))} style={{ width: "100%", height: 64, borderRadius: 999, border: 0, background: C.ink, color: C.white, display: "flex", alignItems: "center", justifyContent: "center", gap: 10, fontSize: 19, fontWeight: 600, cursor: "pointer" }}>
            {last && <Mic />}
            {last ? "Allow microphone" : "Next"}
          </button>
          {last && (
            <button onClick={() => this.finishOnb(false)} style={{ width: "100%", height: 48, border: 0, background: "transparent", color: C.muted, fontSize: 16, fontWeight: 600, cursor: "pointer" }}>Not now</button>
          )}
        </div>
      </>
    );
  }

  renderList() {
    const st = this.state;
    const days = recentDays();
    const dayInfo = days.find((d) => d.offset === st.day) || days[0]!;
    const fromOf: Record<number, string> = {};
    days.forEach((d) => (fromOf[d.offset] = d.from));
    const isToday = st.day === 0;
    const shown = st.items.filter((x) => (isToday ? x.day === 0 || (!x.done && CARRY[x.sec]) : x.day === st.day));
    const open = shown.filter((x) => !x.done).length;
    const sections = SECTIONS.map((name) => ({ name, mine: shown.filter((x) => x.sec === name) })).filter((s) => s.mine.length > 0);
    const countLabel = isToday
      ? dayInfo.date + " · " + (open === 0 ? "Nothing to remember yet." : open + (open === 1 ? " thing to remember" : " things to remember"))
      : dayInfo.date + " · " + shown.length + (shown.length === 1 ? " thing heard" : " things heard");
    const prevDisabled = st.day <= -3;

    return (
      <>
        <div className="v3-scroll" style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto", overflowX: "hidden", boxSizing: "border-box", padding: "72px 28px 24px", display: "flex", flexDirection: "column", gap: 36 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <h1 style={{ margin: 0, fontSize: 34, fontWeight: 600, letterSpacing: "-0.03em" }}>{dayInfo.title}</h1>
                <p style={{ margin: 0, fontSize: 17, color: C.muted }}>{countLabel}</p>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
                <button onClick={() => !prevDisabled && this.setState({ day: st.day - 1, banner: "" })} disabled={prevDisabled} aria-label="Previous day" style={{ ...circleBtn, opacity: prevDisabled ? 0.3 : 1 }}><Chevron dir="l" /></button>
                <button onClick={() => st.day < 0 && this.setState({ day: st.day + 1, banner: "" })} disabled={isToday} aria-label="Next day" style={{ ...circleBtn, opacity: isToday ? 0.3 : 1 }}><Chevron dir="r" /></button>
              </div>
            </div>
            {!isToday && (
              <button className="v3-press v3-in" onClick={() => this.setState({ day: 0 })} style={{ alignSelf: "flex-start", height: 44, padding: "0 18px", borderRadius: 999, border: 0, background: C.soft, color: C.ink, fontSize: 16, fontWeight: 600, cursor: "pointer" }}>Back to today</button>
            )}
          </div>

          {st.banner && (
            <div className="v3-in" role="status" style={{ margin: "-16px 0 -8px", padding: "14px 16px", borderRadius: 16, background: "#EAF7F0", color: "#0B5C37", fontSize: 17, fontWeight: 600, display: "flex", alignItems: "center", gap: 10 }}>
              <span style={dot(10)} />{st.banner}
            </div>
          )}

          {sections.length === 0 && (
            <p style={{ margin: 0, fontSize: 18, lineHeight: 1.5, color: C.muted }}>
              {isToday ? "Nothing here yet. Tap Record and talk. Your notes show up here after you stop." : "Nothing was heard on this day."}
            </p>
          )}

          {sections.map((s) => (
            <section key={s.name} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <h2 style={{ margin: "0 0 6px", display: "flex", justifyContent: "space-between", fontSize: 15, fontWeight: 600, color: C.ink }}>
                <span>{s.name}</span><span style={{ fontWeight: 500, color: C.muted }}>{s.mine.filter((x) => !x.done).length}</span>
              </h2>
              {s.mine.map((x) => {
                let hint = x.hint;
                if (isToday && x.day < 0) hint = fromOf[x.day].replace(/ · $/, "");
                if (!isToday && !x.done && CARRY[x.sec]) hint = "Still on today’s list";
                return (
                  <button key={x.id} role="checkbox" aria-checked={x.done} onClick={() => this.toggleDone(x.id)} style={{ width: "100%", minHeight: 56, display: "flex", gap: 14, alignItems: "flex-start", padding: "10px 0", border: 0, background: "transparent", color: C.ink, textAlign: "left", cursor: "pointer" }}>
                    {x.done ? (
                      <span className="v3-pop" style={{ width: 26, height: 26, flexShrink: 0, borderRadius: "50%", background: C.green, color: C.white, display: "flex", alignItems: "center", justifyContent: "center" }}><Check /></span>
                    ) : (
                      <span style={{ width: 26, height: 26, flexShrink: 0, boxSizing: "border-box", borderRadius: "50%", border: `2px solid ${C.ink}`, display: "block" }} />
                    )}
                    <span style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                      <span style={{ fontSize: 18, lineHeight: 1.3, color: x.done ? C.faint : C.ink, textDecoration: x.done ? "line-through" : "none", transition: "color .2s ease" }}>{x.text}</span>
                      <span style={{ fontSize: 15, color: C.muted }}>
                        {x.isNew && <span style={{ color: C.greenDark, fontWeight: 600 }}>New · </span>}{hint}
                      </span>
                    </span>
                  </button>
                );
              })}
            </section>
          ))}
        </div>

        <div style={{ flexShrink: 0, padding: "12px 24px 34px", display: "flex", gap: 12 }}>
          <button className="v3-press" onClick={() => this.setState({ mode: "chat" })} aria-label="Chat with Wren" style={{ width: 72, height: 72, flexShrink: 0, borderRadius: "50%", border: `1.5px solid ${C.line}`, background: C.white, color: C.ink, display: "flex", alignItems: "center", justifyContent: "center", padding: 0, cursor: "pointer" }}>
            <svg width="26" height="26" {...svgBase} strokeWidth="1.9"><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 3.5V17A1.5 1.5 0 0 1 4 15.5z" /></svg>
          </button>
          <button className="v3-press" onClick={() => void this.start()} style={{ flexGrow: 1, height: 72, borderRadius: 999, border: 0, background: C.ink, color: C.white, display: "flex", alignItems: "center", justifyContent: "center", gap: 12, fontSize: 20, fontWeight: 600, cursor: "pointer" }}>
            <span style={dot(14)} />Record
          </button>
        </div>
      </>
    );
  }

  renderChat() {
    const st = this.state;
    const appsOn = CONNECT_APPS.filter((app) => st.apps[app.key]).length;
    const hasDraft = !!st.draft.trim();
    const roundBtn: CSSProperties = { width: 52, height: 52, flexShrink: 0, borderRadius: "50%", border: 0, background: C.ink, color: C.white, display: "flex", alignItems: "center", justifyContent: "center", padding: 0, cursor: "pointer" };
    return (
      <>
        <div style={{ flexShrink: 0, padding: "58px 20px 12px", display: "flex", alignItems: "center", gap: 12, borderBottom: "1px solid #F0F0F0" }}>
          <button onClick={() => this.setState({ mode: "list" })} aria-label="Back to your list" style={circleBtn}><Chevron dir="l" /></button>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600, letterSpacing: "-0.02em" }}>Wren</h1>
            <span style={{ fontSize: 14, color: C.muted, display: "flex", alignItems: "center", gap: 6 }}><span style={dot(8)} />Knows everything on your list</span>
          </div>
          <button className="v3-press" onClick={() => this.setState({ mode: "apps" })} style={{ marginLeft: "auto", height: 44, padding: "0 14px", borderRadius: 999, border: `1.5px solid ${C.line}`, background: C.white, color: C.ink, display: "flex", alignItems: "center", gap: 8, fontSize: 15, fontWeight: 600, cursor: "pointer" }}>
            <svg width="18" height="18" {...svgBase} strokeWidth="2"><rect x="4" y="4" width="6.5" height="6.5" rx="1.8" /><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.8" /><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.8" /><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.8" /></svg>
            Apps
            {appsOn > 0 && <span style={{ minWidth: 22, height: 22, padding: "0 6px", boxSizing: "border-box", borderRadius: 999, background: C.green, color: C.ink, fontSize: 13, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>{appsOn}</span>}
          </button>
        </div>
        <div className="v3-scroll" style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto", padding: "16px 20px 12px", display: "flex", flexDirection: "column", gap: 12 }}>
          {st.messages.map((message) => (
            message.from === "wren" ? (
              <div key={message.id} style={{ alignSelf: "flex-start", maxWidth: 300, padding: "12px 16px", borderRadius: "20px 20px 20px 6px", background: C.soft, color: C.ink, fontSize: 17, lineHeight: 1.4 }}>{message.text}</div>
            ) : (
              <div key={message.id} style={{ alignSelf: "flex-end", maxWidth: 280, padding: "12px 16px", borderRadius: "20px 20px 6px 20px", background: C.ink, color: C.white, fontSize: 17, lineHeight: 1.4 }}>{message.text}</div>
            )
          ))}
          {(st.asking || st.hearing) && (
            <div aria-label="Wren is writing" style={{ alignSelf: "flex-start", padding: "14px 18px", borderRadius: "20px 20px 20px 6px", background: C.soft, fontSize: 22, letterSpacing: 2, color: C.ink }}>
              <span className="v3-d1">•</span><span className="v3-d2">•</span><span className="v3-d3">•</span>
            </div>
          )}
        </div>
        <div style={{ flexShrink: 0, padding: "10px 0 30px", borderTop: "1px solid #F0F0F0", display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="v3-scroll" style={{ overflowX: "auto", display: "flex", gap: 8, padding: "0 20px" }}>
            {CHIPS.map((chip) => (
              <button key={chip} className="v3-press" onClick={() => this.sendChat(chip)} style={{ flexShrink: 0, height: 40, padding: "0 14px", borderRadius: 999, border: `1.5px solid ${C.line}`, background: C.white, color: C.ink, fontSize: 15, fontWeight: 500, cursor: "pointer", whiteSpace: "nowrap" }}>{chip}</button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", padding: "0 20px" }}>
            <input value={st.draft} onChange={(e) => this.setState({ draft: e.target.value })} onKeyDown={(e) => { if (e.key === "Enter") this.sendChat(this.state.draft); }} placeholder="Ask Wren anything" autoComplete="off" style={{ flexGrow: 1, minWidth: 0, height: 52, boxSizing: "border-box", padding: "0 18px", borderRadius: 999, border: `1.5px solid ${C.line}`, background: C.white, color: C.ink, fontSize: 17, outline: "none" }} />
            {hasDraft ? (
              <button className="v3-press" onClick={() => this.sendChat(this.state.draft)} aria-label="Send" style={roundBtn}>
                <svg width="20" height="20" {...svgBase} strokeWidth="2.4"><line x1="12" y1="19" x2="12" y2="5" /><polyline points="6 11 12 5 18 11" /></svg>
              </button>
            ) : (
              <button className="v3-press" onClick={() => void this.askByVoice()} aria-label={st.hearing ? "Stop and ask" : "Ask by voice"} style={{ ...roundBtn, background: st.hearing ? C.green : C.ink }}>
                {st.hearing ? <span style={{ width: 14, height: 14, borderRadius: 3, background: C.ink, display: "block" }} /> : <Mic />}
              </button>
            )}
          </div>
        </div>
      </>
    );
  }

  renderApps() {
    const st = this.state;
    const ico = { width: 24, height: 24, ...svgBase, stroke: C.ink, strokeWidth: 1.9 };
    return (
      <>
        <div style={{ flexShrink: 0, padding: "58px 20px 12px", display: "flex", alignItems: "center", gap: 12 }}>
          <button onClick={() => this.setState({ mode: "chat" })} aria-label="Back to chat" style={circleBtn}><Chevron dir="l" /></button>
        </div>
        <div className="v3-scroll" style={{ flex: "1 1 0", minHeight: 0, overflowY: "auto", boxSizing: "border-box", padding: "8px 28px 30px", display: "flex", flexDirection: "column", gap: 28 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <h1 style={{ margin: 0, fontSize: 34, fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1.1 }}>Connect your apps</h1>
            <p style={{ margin: 0, fontSize: 18, lineHeight: 1.45, color: C.muted }}>Wren can catch more and remind you in more places. Turn on only what you want.</p>
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {CONNECT_APPS.map((app) => {
              const on = st.apps[app.key];
              return (
                <div key={app.key} style={{ display: "flex", gap: 14, alignItems: "flex-start", padding: "18px 0", borderTop: "1px solid #F0F0F0" }}>
                  <span style={{ width: 48, height: 48, flexShrink: 0, borderRadius: 14, background: C.soft, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    {app.icon === "mail" && <svg {...ico}><rect x="3.5" y="5.5" width="17" height="13" rx="2.5" /><polyline points="4.5 7.5 12 13 19.5 7.5" /></svg>}
                    {app.icon === "cal" && <svg {...ico}><rect x="4" y="5" width="16" height="15" rx="3" /><line x1="4" y1="10" x2="20" y2="10" /><line x1="9" y1="3" x2="9" y2="7" /><line x1="15" y1="3" x2="15" y2="7" /></svg>}
                    {app.icon === "chat" && <svg {...ico}><path d="M5 19.5l1.3-3.8A7.5 7.5 0 1 1 9.2 18.4z" /></svg>}
                    {app.icon === "work" && <svg {...ico}><line x1="9.5" y1="4" x2="7.5" y2="20" /><line x1="16.5" y1="4" x2="14.5" y2="20" /><line x1="4.5" y1="9" x2="20" y2="9" /><line x1="4" y1="15" x2="19.5" y2="15" /></svg>}
                  </span>
                  <div style={{ flexGrow: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                    <span style={{ fontSize: 18, fontWeight: 600 }}>{app.name}</span>
                    <span style={{ fontSize: 15, lineHeight: 1.4, color: C.muted }}>{app.desc}</span>
                    {on && <span style={{ fontSize: 14, fontWeight: 600, color: C.greenDark, display: "flex", alignItems: "center", gap: 6 }}><Check w={3} />Connected</span>}
                  </div>
                  <button role="switch" aria-checked={on} aria-label={(on ? "Turn off " : "Connect ") + app.name} onClick={() => this.toggleApp(app.key)} style={{ position: "relative", width: 56, height: 34, flexShrink: 0, marginTop: 7, borderRadius: 999, border: 0, padding: 0, background: on ? C.green : "#D4D4D4", cursor: "pointer" }}>
                    <span style={{ position: "absolute", top: 4, left: on ? 26 : 4, width: 26, height: 26, borderRadius: "50%", background: C.white, boxShadow: "0 1px 3px rgba(0,0,0,0.25)", display: "block" }} />
                  </button>
                </div>
              );
            })}
          </div>
          <div style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: 16, borderRadius: 16, background: "#F7F7F7" }}>
            <svg width="20" height="20" {...svgBase} stroke={C.ink} strokeWidth="1.8" style={{ flexShrink: 0, marginTop: 1 }}><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
            <span style={{ fontSize: 15, lineHeight: 1.45, color: "#3D3D3D" }}>Wren only looks for things you need to do or remember. It never sends a message without asking you first.</span>
          </div>
        </div>
      </>
    );
  }

  renderRec() {
    const st = this.state;
    const holding = st.hold > 0;
    const isKey = (e: KeyboardEvent) => e.key === "Enter" || e.key === " ";
    return (
      <>
        <div className="v3-in" style={{ flex: "1 1 0", minHeight: 0, boxSizing: "border-box", padding: "72px 28px 24px", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 20, textAlign: "center" }}>
          <span className="v3-glow" style={dot(16)} />
          <div style={{ fontSize: 72, fontWeight: 500, letterSpacing: "-0.04em", lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{pad(Math.floor(st.elapsed / 60)) + ":" + pad(st.elapsed % 60)}</div>
          <p role="status" style={{ margin: 0, fontSize: 20, lineHeight: 1.4, color: st.tapHint ? C.white : "#BDBDBD" }}>
            {st.saving ? "Writing your list…" : holding ? "Almost done…" : st.tapHint ? "Keep your finger on the button to stop." : "Listening. Just talk normally."}
          </p>
        </div>
        <div style={{ flexShrink: 0, padding: "0 24px 34px", display: "flex", flexDirection: "column", gap: 14 }}>
          <button
            disabled={st.saving}
            onPointerDown={(e: PointerEvent) => this.holdStart(e)}
            onPointerUp={() => this.holdEnd()}
            onPointerLeave={() => this.holdEnd()}
            onPointerCancel={() => this.holdEnd()}
            onKeyDown={(e) => { if (isKey(e) && !e.repeat) { e.preventDefault(); this.holdStart(); } }}
            onKeyUp={(e) => { if (isKey(e)) { e.preventDefault(); this.holdEnd(); } }}
            onContextMenu={(e) => e.preventDefault()}
            aria-label="Press and hold to stop recording"
            style={{ position: "relative", overflow: "hidden", width: "100%", height: 72, borderRadius: 999, border: 0, background: C.white, color: C.ink, display: "flex", alignItems: "center", justifyContent: "center", gap: 12, fontSize: 20, fontWeight: 600, cursor: st.saving ? "default" : "pointer", touchAction: "none", userSelect: "none", WebkitUserSelect: "none", WebkitTouchCallout: "none" } as CSSProperties}
          >
            <span aria-hidden="true" style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${st.hold}%`, background: C.green, display: "block" }} />
            <span style={{ position: "relative", width: 14, height: 14, borderRadius: 3, background: C.ink, display: "block" }} />
            <span style={{ position: "relative" }}>{st.saving ? "Writing your list…" : holding ? "Keep holding…" : "Hold to stop"}</span>
          </button>
          <p style={{ margin: 0, textAlign: "center", fontSize: 15, color: "#9A9A9A" }}>Press and hold to stop</p>
        </div>
      </>
    );
  }

  override render() {
    const st = this.state;
    return (
      <div className="wren-root" style={{ position: "relative", width: "100%", height: "100%", overflow: "hidden", background: st.recording ? C.ink : C.white, color: st.recording ? C.white : C.ink, display: "flex", flexDirection: "column", transition: "background .35s ease" }}>
        {st.recording ? this.renderRec()
          : st.mode === "onb" ? this.renderOnb()
          : st.mode === "chat" ? this.renderChat()
          : st.mode === "apps" ? this.renderApps()
          : this.renderList()}
      </div>
    );
  }
}
