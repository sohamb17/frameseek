import { useEffect, useRef, useState } from "react";
import { Bookmark, Minus, Pause, Play, Plus, RotateCcw, Scissors } from "lucide-react";
import clsx from "clsx";
import { fmtTime } from "../lib/format";

export interface Interval { start_ms: number; end_ms: number }

interface Props {
  src: string;
  title: string;
  durationMs: number;
  interval: Interval;
  /** Other result intervals in the same video, drawn as markers on the timeline. */
  markers?: (Interval & { rank?: number })[];
  editable?: boolean;
  onSave?: (iv: Interval) => void;
  saveLabel?: string;
  autoPlay?: boolean;
  /** Reports the playhead (ms) to parents such as the labeling tool. */
  onTime?: (ms: number) => void;
  playerRef?: React.MutableRefObject<HTMLVideoElement | null>;
  /** Frame shown before playback starts (the matched frame for search results). */
  poster?: string;
  /**
   * Where this video's timeline starts inside `src` (ms). Non-zero only in the static demo, which
   * plays excerpts from the full original recording; all times shown and passed around stay
   * relative to the excerpt.
   */
  offsetMs?: number;
}

const STEP = 2000;

export default function Player({ src, title, durationMs, interval, markers = [], editable = true, onSave,
  saveLabel = "Save moment", autoPlay = true, onTime, playerRef, poster, offsetMs = 0 }: Props) {
  const ref = useRef<HTMLVideoElement | null>(null);
  const [now, setNow] = useState(interval.start_ms);
  const [playing, setPlaying] = useState(false);
  const [stopAtEnd, setStopAtEnd] = useState(true);
  const [iv, setIv] = useState<Interval>(interval);
  const [dur, setDur] = useState(durationMs || interval.end_ms);
  const edited = iv.start_ms !== interval.start_ms || iv.end_ms !== interval.end_ms;

  const lastSrc = useRef<string | null>(null);
  // A new result was selected: reset the editable interval and seek to its start.
  // Without autoPlay (labeling tool) only a new video triggers a seek, so marking IN/OUT never jumps.
  useEffect(() => {
    setIv(interval);
    const v = ref.current;
    const srcChanged = lastSrc.current !== src;
    lastSrc.current = src;
    if (!v || (!autoPlay && !srcChanged)) return;
    const seek = () => {
      v.currentTime = (interval.start_ms + offsetMs) / 1000;
      if (autoPlay) v.play().catch(() => undefined);
    };
    if (v.readyState >= 1) seek();
    else v.addEventListener("loadedmetadata", seek, { once: true });
  }, [src, interval.start_ms, interval.end_ms, autoPlay]);

  useEffect(() => {
    if (playerRef) playerRef.current = ref.current;
  });

  const onTimeUpdate = () => {
    const v = ref.current!;
    const ms = v.currentTime * 1000 - offsetMs;
    setNow(ms);
    onTime?.(ms);
    if (stopAtEnd && !v.paused && ms >= iv.end_ms && ms - iv.end_ms < 1500) v.pause();
  };

  const playClip = () => {
    const v = ref.current!;
    v.currentTime = (iv.start_ms + offsetMs) / 1000;
    v.play().catch(() => undefined);
  };
  const toggle = () => {
    const v = ref.current!;
    if (v.paused) v.play().catch(() => undefined);
    else v.pause();
  };
  const clamp = (x: number) => Math.max(0, Math.min(dur, x));
  const nudge = (which: "start" | "end", d: number) =>
    setIv((cur) => {
      const next = { ...cur, [which === "start" ? "start_ms" : "end_ms"]: clamp((which === "start" ? cur.start_ms : cur.end_ms) + d) };
      return next.end_ms - next.start_ms >= 1000 ? next : cur;
    });
  const setHere = (which: "start" | "end") =>
    setIv((cur) => {
      const t = Math.round(now);
      const next = which === "start" ? { start_ms: t, end_ms: Math.max(cur.end_ms, t + 1000) } : { start_ms: Math.min(cur.start_ms, t - 1000), end_ms: t };
      return { start_ms: clamp(next.start_ms), end_ms: clamp(next.end_ms) };
    });

  const pct = (ms: number) => `${(100 * ms) / Math.max(1, dur)}%`;
  const seekTo = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const v = ref.current!;
    v.currentTime = (((e.clientX - r.left) / r.width) * dur + offsetMs) / 1000;
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="relative overflow-hidden rounded-xl bg-black ring-1 ring-ink-800">
        <video
          ref={ref}
          src={src}
          poster={poster}
          className="aspect-video w-full bg-black object-contain"
          controls
          preload="metadata"
          onTimeUpdate={onTimeUpdate}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onLoadedMetadata={(e) => setDur(offsetMs || !e.currentTarget.duration ? durationMs || dur : e.currentTarget.duration * 1000)}
        />
      </div>

      {/* timeline: the whole video, the matched interval, other results, and the playhead */}
      <div>
        <div className="relative h-7 cursor-pointer rounded-md bg-ink-850 ring-1 ring-ink-800" onClick={seekTo} title="Click to seek">
          {markers.map((m, i) => (
            <div key={i} className="absolute top-1 bottom-1 rounded-sm bg-slate-500/30"
              style={{ left: pct(m.start_ms), width: pct(m.end_ms - m.start_ms) }} title={m.rank ? `Result #${m.rank}` : undefined} />
          ))}
          <div className="absolute top-0 bottom-0 rounded-md bg-gradient-to-r from-sky-500/70 via-violet-500/70 to-amber-500/70 ring-1 ring-white/20"
            style={{ left: pct(iv.start_ms), width: `max(3px, ${pct(iv.end_ms - iv.start_ms)})` }} />
          <div className="absolute top-[-3px] bottom-[-3px] w-0.5 bg-white shadow-[0_0_6px_white]" style={{ left: pct(now) }} />
        </div>
        <div className="mt-1 flex justify-between font-mono text-[11px] text-slate-500">
          <span>0:00</span>
          <span className="text-slate-300">{fmtTime(now)}</span>
          <span>{fmtTime(dur)}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-primary" onClick={playClip}><Play size={14} /> Play clip</button>
        <button className="btn btn-outline" onClick={toggle}>{playing ? <Pause size={14} /> : <Play size={14} />}{playing ? "Pause" : "Resume"}</button>
        <label className="ml-1 flex items-center gap-1.5 text-xs text-slate-400 select-none">
          <input type="checkbox" checked={stopAtEnd} onChange={(e) => setStopAtEnd(e.target.checked)} className="accent-sky-500" />
          Stop at clip end
        </label>
        <span className="ml-auto font-mono text-xs text-slate-400">
          {fmtTime(iv.start_ms)} – {fmtTime(iv.end_ms)} <span className="text-slate-600">({Math.round((iv.end_ms - iv.start_ms) / 1000)} s)</span>
        </span>
      </div>

      {editable && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-800 bg-ink-850/60 p-2.5">
          <Scissors size={14} className="text-slate-500" />
          <span className="text-xs text-slate-400">Adjust boundaries</span>
          <div className="flex items-center gap-1">
            <span className="text-[11px] text-slate-500">start</span>
            <button className="btn btn-ghost px-1.5" onClick={() => nudge("start", -STEP)}><Minus size={12} /></button>
            <button className="btn btn-ghost px-1.5" onClick={() => nudge("start", STEP)}><Plus size={12} /></button>
            <button className="btn btn-ghost px-2 text-xs" onClick={() => setHere("start")}>= now</button>
          </div>
          <div className="flex items-center gap-1">
            <span className="text-[11px] text-slate-500">end</span>
            <button className="btn btn-ghost px-1.5" onClick={() => nudge("end", -STEP)}><Minus size={12} /></button>
            <button className="btn btn-ghost px-1.5" onClick={() => nudge("end", STEP)}><Plus size={12} /></button>
            <button className="btn btn-ghost px-2 text-xs" onClick={() => setHere("end")}>= now</button>
          </div>
          {edited && (
            <button className="btn btn-ghost text-xs" onClick={() => setIv(interval)} title="Back to the predicted interval">
              <RotateCcw size={12} /> reset
            </button>
          )}
          {onSave && (
            <button className={clsx("btn ml-auto", edited ? "btn-primary" : "btn-outline")} onClick={() => onSave(iv)}>
              <Bookmark size={14} /> {saveLabel}{edited ? " (edited)" : ""}
            </button>
          )}
        </div>
      )}
      <div className="sr-only">{title}</div>
    </div>
  );
}
