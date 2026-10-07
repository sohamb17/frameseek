import { useEffect, useRef, useState } from "react";
import { Bot, CheckCircle2, ClipboardCheck, Info, Play, Plus, SkipForward, Trash2, X, XCircle } from "lucide-react";
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

type Answer = { video_id: string; title: string; start_ms: number; end_ms: number };
const isAI = (l: EvalQuery) => l.author !== "owner";
/** Waiting for a person: an assistant draft, or an assistant label drawn into the random spot-check sample. */
const needsReview = (l: EvalQuery) => !l.reviewed_at && (l.author === "assistant-draft" || (l.author === "assistant" && l.spot_check));
const isUsable = (l: EvalQuery) => l.review_verdict !== "rejected" && !(l.author === "assistant-draft" && !l.reviewed_at);

/** Write evaluation queries while watching the video, before ever seeing search results for them.
 *  Also the review step for assistant-drafted labels: a draft is only used once a person has watched
 *  its interval here and saved it (which stamps reviewed_at on the server). */
export default function LabelPage() {
  const toast = useToast();
  const [videos, setVideos] = useState<Video[]>([]);
  const [vid, setVid] = useState("");
  const [timeline, setTimeline] = useState<TimelineSegment[]>([]);
  const [now, setNow] = useState(0);
  const [inMs, setIn] = useState<number | null>(null);
  const [outMs, setOut] = useState<number | null>(null);
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [editing, setEditing] = useState<EvalQuery | null>(null);
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
  useEffect(() => { if (vid) api.timeline(vid).then(setTimeline).catch(() => setTimeline([])); }, [vid]);
  const video = videos.find((v) => v.id === vid);

  const pickVideo = (id: string) => { setVid(id); setIn(null); setOut(null); };
  // Show an interval: mark it as IN/OUT and seek there. On a video switch the player seeks to IN itself.
  const jumpTo = (a: { video_id: string; start_ms: number; end_ms: number }) => {
    setIn(a.start_ms); setOut(a.end_ms);
    if (a.video_id !== vid) setVid(a.video_id);
    else if (player.current) player.current.currentTime = a.start_ms / 1000;
  };
  const resetForm = () => { setEditing(null); setQuery(""); setNotes(""); setAnswers([]); setIn(null); setOut(null); };
  const openLabel = (l: EvalQuery) => {
    setEditing(l);
    setQuery(l.query); setQtype(l.query_type); setNotes(l.notes);
    setAnswers(l.answers.map((a) => ({ video_id: a.video_id, title: a.video_title, start_ms: a.start_ms, end_ms: a.end_ms })));
    if (l.answers[0]) jumpTo(l.answers[0]);
  };
  // Review drafts video by video, in timeline order, so the player rarely has to switch sources.
  // No-answer drafts come last.
  const drafts = labels.filter(needsReview).sort((a, b) =>
    (a.answers.length ? 0 : 1) - (b.answers.length ? 0 : 1) ||
    (a.answers[0]?.video_title ?? "").localeCompare(b.answers[0]?.video_title ?? "") ||
    (a.answers[0]?.start_ms ?? 0) - (b.answers[0]?.start_ms ?? 0) || a.query.localeCompare(b.query));
  // The draft after `after` in review order (wrapping), or the first one.
  const nextDraft = (after?: string) => {
    const i = drafts.findIndex((d) => d.id === after);
    const rest = drafts.filter((d) => d.id !== after);
    if (rest.length) openLabel(rest[i >= 0 ? i % rest.length : 0]); else resetForm();
  };

  const addAnswer = () => {
    if (inMs == null || outMs == null || outMs <= inMs || !video) return;
    setAnswers((a) => [...a, { video_id: vid, title: video.title, start_ms: Math.round(inMs), end_ms: Math.round(outMs) }]);
    setIn(null); setOut(null);
  };
  const replaceAnswer = (i: number) => {
    if (inMs == null || outMs == null || outMs <= inMs || !video) return;
    setAnswers((a) => a.map((x, j) => (j === i ? { video_id: vid, title: video.title, start_ms: Math.round(inMs), end_ms: Math.round(outMs) } : x)));
  };
  const save = async () => {
    const body = { query, query_type: qtype, notes, answers: answers.map(({ video_id, start_ms, end_ms }) => ({ video_id, start_ms, end_ms })) };
    try {
      if (editing) {
        const r = await api.updateEvalQuery(editing.id, body) as { review_verdict?: string | null };
        toast(needsReview(editing) ? (r.review_verdict === "corrected" ? "Saved as corrected" : "Accepted as is") : "Label updated");
        const wasDraft = needsReview(editing);
        await loadLabels();
        if (wasDraft) nextDraft(editing.id); else resetForm();
      } else {
        await api.addEvalQuery(body);
        toast("Label saved");
        resetForm();
        loadLabels();
      }
    } catch (e) { toast((e as Error).message, "err"); }
  };
  const reject = async (l: EvalQuery) => {
    try {
      await api.rejectEvalQuery(l.id);
      toast("Rejected: it will not be used, and it counts against the label set's agreement");
      const was = needsReview(l);
      await loadLabels();
      if (was) nextDraft(l.id); else resetForm();
    } catch (e) { toast((e as Error).message, "err"); }
  };
  const remove = async (l: EvalQuery) => {
    await api.deleteEvalQuery(l.id);
    await loadLabels();
    if (editing?.id === l.id) { if (needsReview(l)) nextDraft(l.id); else resetForm(); }
  };
  const usable = labels.filter(isUsable);
  const counts = QTYPES.map((t) => ({ ...t, n: usable.filter((l) => l.query_type === t.v).length }));
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
        <select className="input w-full" value={vid} onChange={(e) => pickVideo(e.target.value)}>
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
        {drafts.length > 0 && !editing && (
          <div className="card flex items-center gap-3 border-amber-500/30 p-3 text-xs text-amber-100/80">
            <Bot size={16} className="shrink-0 text-amber-300" />
            <div className="flex-1"><b>{drafts.length} AI-written labels</b> are waiting for your check (a random spot-check sample, or drafts).
              Training and evaluation wait until they are done.</div>
            <button className="btn btn-outline shrink-0 py-1 text-xs" onClick={() => nextDraft()}><Play size={12} /> Start review</button>
          </div>
        )}
        <div className={clsx("card space-y-3 p-4", editing && needsReview(editing) && "ring-1 ring-amber-500/40")}>
          <div className="flex items-center gap-2">
            <div className="label flex-1">{editing ? (needsReview(editing) ? (editing.spot_check ? "Spot-check: AI-written label" : "Reviewing assistant draft") : "Edit label") : "New labeled query"}</div>
            {editing && needsReview(editing) && <span className="text-[11px] text-slate-500">{drafts.length} left</span>}
            {editing && <button className="text-slate-500 hover:text-white" title="Cancel" onClick={resetForm}><X size={14} /></button>}
          </div>
          {editing && needsReview(editing) && (
            <div className="rounded-lg bg-amber-500/10 px-3 py-2 text-[11.5px] leading-relaxed text-amber-100/80">
              Play the clip and judge it. <b>Accept as is</b> if the clip answers the query and its edges are within about 2 s.
              Fix it and save only if the query is misleading, the type is wrong, or the clip misses or overshoots the answer
              (set IN/OUT, then <b>use IN/OUT</b> on the answer). <b>Reject</b> it if it is simply wrong.
              Your verdicts are reported as the label set's measured agreement.
              {editing.notes && <div className="mt-1 text-amber-200/60">Note: {editing.notes}</div>}
            </div>
          )}
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
                <button title="Play this interval" className="text-sky-300 hover:text-white" onClick={() => jumpTo(a)}><Play size={12} /></button>
                <span className="flex-1 truncate text-slate-300">{a.title}</span>
                <span className="font-mono text-slate-400">{fmtTime(a.start_ms)}–{fmtTime(a.end_ms)}</span>
                <button title="Replace this interval with the current IN/OUT" disabled={inMs == null || outMs == null || outMs <= inMs}
                  className="rounded px-1 text-[10.5px] text-sky-300 ring-1 ring-ink-700 hover:text-white disabled:opacity-30" onClick={() => replaceAnswer(i)}>use IN/OUT</button>
                <button title="Remove" onClick={() => setAnswers((x) => x.filter((_, j) => j !== i))}><X size={12} /></button>
              </div>
            ))}
            {qtype !== "no_answer" && answers.length === 0 && <div className="text-xs text-slate-600">Add at least one answer interval from the player.</div>}
          </div>
          <input className="input w-full text-xs" placeholder="notes (ambiguity, alternate answers…)" value={notes} onChange={(e) => setNotes(e.target.value)} />
          {editing ? (
            <div className="flex gap-2">
              <button className="btn btn-primary flex-1 justify-center" disabled={!canSave} onClick={save}>
                <CheckCircle2 size={14} /> {needsReview(editing) ? "Accept / save" : "Save changes"}
              </button>
              {needsReview(editing) && drafts.length > 1 && (
                <button className="btn btn-outline" title="Skip for now" onClick={() => nextDraft(editing.id)}><SkipForward size={14} /></button>
              )}
              {isAI(editing)
                ? <button className="btn btn-outline hover:text-rose-300" title="Reject: the label is wrong" onClick={() => reject(editing)}><XCircle size={14} /> Reject</button>
                : <button className="btn btn-outline hover:text-rose-300" title="Delete this label" onClick={() => remove(editing)}><Trash2 size={14} /></button>}
            </div>
          ) : (
            <button className="btn btn-primary w-full justify-center" disabled={!canSave} onClick={save}><ClipboardCheck size={14} /> Save label</button>
          )}
        </div>
        <div className="card p-4">
          <div className="flex items-center justify-between"><div className="label">Label set</div>
            <div className="text-xs text-slate-500">{usable.length} usable{drafts.length ? ` · ${drafts.length} to check` : ""}</div></div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {counts.map((c) => <span key={c.v} className="rounded-md bg-ink-850 px-2 py-0.5 text-xs text-slate-400">{c.label}: <b className="text-slate-200">{c.n}</b></span>)}
          </div>
          <div className="mt-3 max-h-72 space-y-1 overflow-y-auto">
            {[...drafts, ...labels.filter((l) => !needsReview(l))].map((l) => (
              <div key={l.id} onClick={() => openLabel(l)}
                className={clsx("group flex cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-ink-850", editing?.id === l.id && "bg-ink-850")}>
                <span className="mt-0.5 rounded bg-ink-800 px-1.5 text-[10px] text-slate-400">{l.query_type}</span>
                <div className="min-w-0 flex-1">
                  <div className={clsx(needsReview(l) ? "text-slate-400" : "text-slate-200", l.review_verdict === "rejected" && "line-through opacity-60")}>
                    {l.query}
                    {isAI(l) && <span className="ml-1.5 rounded bg-violet-500/15 px-1 text-[10px] text-violet-300">AI</span>}
                    {needsReview(l) && <span className="ml-1 rounded bg-amber-500/15 px-1 text-[10px] text-amber-300">{l.spot_check ? "spot-check" : "draft"}</span>}
                    {l.review_verdict && <span className={clsx("ml-1 rounded px-1 text-[10px]", l.review_verdict === "rejected" ? "bg-rose-500/15 text-rose-300" : "bg-emerald-500/10 text-emerald-300/80")}>{l.review_verdict}</span>}
                  </div>
                  {l.answers.map((a, i) => <div key={i} className="truncate text-slate-500">{a.video_title} · {fmtTime(a.start_ms)}–{fmtTime(a.end_ms)}</div>)}
                </div>
                {!isAI(l) && <button className="opacity-0 group-hover:opacity-100 hover:text-rose-300" onClick={(e) => { e.stopPropagation(); remove(l); }}><Trash2 size={12} /></button>}
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
