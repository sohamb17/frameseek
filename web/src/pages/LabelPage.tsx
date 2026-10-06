import { useEffect, useRef, useState } from "react";
import { ClipboardCheck, Info, Plus, Trash2, X } from "lucide-react";
import clsx from "clsx";
import { api, type EvalQuery, type TimelineSegment, type Video } from "../lib/api";
import { fmtTime } from "../lib/format";
import Player from "../components/Player";
import { useToast } from "../components/Toast";

const QTYPES = [
  { v: "speech", label: "Speech", hint: "answer is in what is said" },
  { v: "ocr", label: "On-screen text", hint: "answer is text visible on screen" },
  { v: "visual", label: "Visual", hint: "answer is something shown, not said or written" },
  { v: "mixed", label: "Mixed", hint: "needs more than one modality" },
  { v: "no_answer", label: "No answer", hint: "nothing in the library answers it" },
];

/** Write evaluation queries while watching the video, before ever seeing search results for them. */
export default function LabelPage() {
  const toast = useToast();
  const [videos, setVideos] = useState<Video[]>([]);
  const [vid, setVid] = useState("");
  const [timeline, setTimeline] = useState<TimelineSegment[]>([]);
  const [now, setNow] = useState(0);
  const [inMs, setIn] = useState<number | null>(null);
  const [outMs, setOut] = useState<number | null>(null);
  const [answers, setAnswers] = useState<{ video_id: string; title: string; start_ms: number; end_ms: number }[]>([]);
  const [query, setQuery] = useState("");
  const [qtype, setQtype] = useState("speech");
  const [notes, setNotes] = useState("");
  const [labels, setLabels] = useState<EvalQuery[]>([]);
  const player = useRef<HTMLVideoElement | null>(null);

  const loadLabels = () => api.evalQueries().then(setLabels).catch(() => undefined);
  useEffect(() => {
    api.videos().then((v) => { const r = v.filter((x) => x.active_index_version != null); setVideos(r); if (r[0]) setVid(r[0].id); });
    loadLabels();
  }, []);
  useEffect(() => { if (vid) api.timeline(vid).then(setTimeline).catch(() => setTimeline([])); setIn(null); setOut(null); }, [vid]);
  const video = videos.find((v) => v.id === vid);

  const addAnswer = () => {
    if (inMs == null || outMs == null || outMs <= inMs || !video) return;
    setAnswers((a) => [...a, { video_id: vid, title: video.title, start_ms: Math.round(inMs), end_ms: Math.round(outMs) }]);
    setIn(null); setOut(null);
  };
  const save = async () => {
    try {
      await api.addEvalQuery({ query, query_type: qtype, notes, answers: answers.map(({ video_id, start_ms, end_ms }) => ({ video_id, start_ms, end_ms })) });
      toast("Label saved");
      setQuery(""); setNotes(""); setAnswers([]);
      loadLabels();
    } catch (e) { toast((e as Error).message, "err"); }
  };
  const counts = QTYPES.map((t) => ({ ...t, n: labels.filter((l) => l.query_type === t.v).length }));
  const canSave = query.trim().length > 2 && (qtype === "no_answer" ? answers.length === 0 : answers.length > 0);

  return (
    <div className="mx-auto grid max-w-[1400px] gap-5 px-4 pb-16 pt-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div className="space-y-3">
        <div>
          <h2 className="text-2xl font-bold text-white">Label relevance</h2>
          <p className="text-sm text-slate-400">The learned scorer and every reported metric depend on these human labels.</p>
        </div>
        <div className="card flex gap-2 p-3 text-xs leading-relaxed text-slate-400">
          <Info size={15} className="mt-0.5 shrink-0 text-sky-300" />
          <div>Watch the video first, then write the query a real user would type and mark the <b>minimum useful interval</b> (aim for 5-30 s).
            Write queries <b>before</b> searching for them, cover all five types, and add every valid answer if a moment appears more than once.</div>
        </div>
        <select className="input w-full" value={vid} onChange={(e) => setVid(e.target.value)}>
          {videos.map((v) => <option key={v.id} value={v.id}>{v.title}</option>)}
        </select>
        {video?.playback_url && (
          <div className="card p-3">
            <Player src={video.playback_url} title={video.title} durationMs={video.duration_ms ?? 0} editable={false} autoPlay={false}
              interval={{ start_ms: inMs ?? 0, end_ms: outMs ?? (inMs ?? 0) + 1000 }} onTime={setNow} playerRef={player} />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button className="btn btn-outline" onClick={() => setIn(now)}>Set IN ({fmtTime(inMs ?? now)})</button>
              <button className="btn btn-outline" onClick={() => setOut(now)}>Set OUT ({fmtTime(outMs ?? now)})</button>
              <button className="btn btn-primary" disabled={inMs == null || outMs == null || outMs <= inMs} onClick={addAnswer}><Plus size={14} /> Add answer interval</button>
            </div>
          </div>
        )}
        <div className="card max-h-72 overflow-y-auto p-2">
          <div className="label px-1 pb-1">Transcript & screen text (click to seek)</div>
          {timeline.filter((s) => s.idx % 2 === 0).map((s) => (
            <div key={s.id} onClick={() => { if (player.current) player.current.currentTime = s.start_ms / 1000; }}
              className="cursor-pointer rounded-lg px-2 py-1.5 text-xs hover:bg-ink-850">
              <span className="mr-2 font-mono text-sky-300">{fmtTime(s.start_ms)}</span>
              <span className="text-slate-300">{s.transcript || <i className="text-slate-600">no speech</i>}</span>
              {s.ocr_text && <div className="mt-0.5 line-clamp-1 font-mono text-[10.5px] text-amber-200/60">{s.ocr_text.replace(/\n/g, " · ")}</div>}
            </div>
          ))}
        </div>
      </div>

      <div className="space-y-3 lg:sticky lg:top-20 lg:self-start">
        <div className="card space-y-3 p-4">
          <div className="label">New labeled query</div>
          <input className="input w-full" placeholder="e.g. where do they connect the probe to the board" value={query} onChange={(e) => setQuery(e.target.value)} />
          <div className="flex flex-wrap gap-1.5">
            {QTYPES.map((t) => (
              <button key={t.v} title={t.hint} onClick={() => setQtype(t.v)} className={clsx("rounded-full px-2.5 py-1 text-xs ring-1",
                qtype === t.v ? "bg-sky-500/15 text-sky-300 ring-sky-500/40" : "text-slate-400 ring-ink-700")}>{t.label}</button>
            ))}
          </div>
          <div className="space-y-1">
            {answers.map((a, i) => (
              <div key={i} className="flex items-center gap-2 rounded-lg bg-ink-850 px-2 py-1 text-xs">
                <span className="flex-1 truncate text-slate-300">{a.title}</span>
                <span className="font-mono text-slate-400">{fmtTime(a.start_ms)}–{fmtTime(a.end_ms)}</span>
                <button onClick={() => setAnswers((x) => x.filter((_, j) => j !== i))}><X size={12} /></button>
              </div>
            ))}
            {qtype !== "no_answer" && answers.length === 0 && <div className="text-xs text-slate-600">Add at least one answer interval from the player.</div>}
          </div>
          <input className="input w-full text-xs" placeholder="notes (ambiguity, alternate answers…)" value={notes} onChange={(e) => setNotes(e.target.value)} />
          <button className="btn btn-primary w-full justify-center" disabled={!canSave} onClick={save}><ClipboardCheck size={14} /> Save label</button>
        </div>
        <div className="card p-4">
          <div className="flex items-center justify-between"><div className="label">Label set</div><div className="text-xs text-slate-500">{labels.length} queries</div></div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {counts.map((c) => <span key={c.v} className="rounded-md bg-ink-850 px-2 py-0.5 text-xs text-slate-400">{c.label}: <b className="text-slate-200">{c.n}</b></span>)}
          </div>
          <div className="mt-3 max-h-72 space-y-1 overflow-y-auto">
            {labels.map((l) => (
              <div key={l.id} className="group flex items-start gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-ink-850">
                <span className="mt-0.5 rounded bg-ink-800 px-1.5 text-[10px] text-slate-400">{l.query_type}</span>
                <div className="min-w-0 flex-1">
                  <div className="text-slate-200">{l.query}</div>
                  {l.answers.map((a, i) => <div key={i} className="truncate text-slate-500">{a.video_title} · {fmtTime(a.start_ms)}–{fmtTime(a.end_ms)}</div>)}
                </div>
                <button className="opacity-0 group-hover:opacity-100 hover:text-rose-300" onClick={async () => { await api.deleteEvalQuery(l.id); loadLabels(); }}><Trash2 size={12} /></button>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-600">
            Export to Git with <code className="text-slate-400">docker compose exec worker python -m frameseek.eval.labels export</code>, then freeze splits and train (see README).
          </p>
        </div>
      </div>
    </div>
  );
}
