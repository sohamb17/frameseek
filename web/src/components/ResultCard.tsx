import { AlertCircle, Expand, ThumbsDown, ThumbsUp } from "lucide-react";
import clsx from "clsx";
import type { SearchResult } from "../lib/api";
import { CONTENT_LABEL, fmtTime } from "../lib/format";
import { Highlighted, ModalityChip, StrengthBars } from "./Evidence";

interface Props {
  r: SearchResult;
  active?: boolean;
  compact?: boolean;
  onSelect: () => void;
  feedback?: "relevant" | "irrelevant";
  onFeedback?: (label: "relevant" | "irrelevant") => void;
}

export default function ResultCard({ r, active, compact, onSelect, feedback, onFeedback }: Props) {
  const thumb = r.evidence.visual?.thumb_url ?? r.thumb_url;
  return (
    <div
      onClick={onSelect}
      className={clsx("group card cursor-pointer p-3 transition-all hover:border-ink-700 hover:bg-ink-850/80",
        active && "border-sky-500/50 ring-1 ring-sky-500/30 bg-ink-850/90")}
    >
      <div className="flex gap-3">
        <div className="relative shrink-0">
          {thumb ? (
            <img src={thumb} alt="" className={clsx("rounded-lg object-cover ring-1 ring-ink-800", compact ? "h-16 w-28" : "h-20 w-36")} loading="lazy" />
          ) : (
            <div className={clsx("rounded-lg bg-ink-800", compact ? "h-16 w-28" : "h-20 w-36")} />
          )}
          <span className="absolute bottom-1 right-1 rounded bg-black/75 px-1.5 py-0.5 font-mono text-[10px] text-white">
            {fmtTime(r.start_ms)}
          </span>
          <span className="absolute left-1 top-1 rounded bg-black/70 px-1.5 text-[10px] font-semibold text-slate-200">#{r.rank}</span>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold text-slate-100">{r.video_title}</div>
              <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
                <span className="font-mono text-slate-400">{fmtTime(r.start_ms)} – {fmtTime(r.end_ms)}</span>
                <span>·</span>
                <span>{CONTENT_LABEL[r.content_type]}</span>
                {r.expanded_from && <span className="inline-flex items-center gap-1 text-violet-300"><Expand size={11} /> expanded</span>}
                {r.low_relevance && <span className="inline-flex items-center gap-1 text-amber-300"><AlertCircle size={11} /> low relevance</span>}
              </div>
            </div>
            {!compact && <StrengthBars s={r.modality_strength} />}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {(["speech", "screen_text", "visual"] as const).map((m) => (
              <ModalityChip key={m} m={m} dim={!r.evidence_types.includes(m)} />
            ))}
          </div>
        </div>
      </div>
      {!compact && (
        <div className="mt-2.5 space-y-1.5 text-[13px] leading-relaxed">
          {r.evidence.transcript_missing ? (
            <p className="italic text-slate-600">No speech in this window.</p>
          ) : (
            <p className="line-clamp-3 text-slate-300"><Highlighted text={`“${r.evidence.transcript}”`} /></p>
          )}
          {!r.evidence.ocr_missing && (
            <p className="mark-screen line-clamp-2 rounded-md bg-ink-850 px-2 py-1 font-mono text-[11.5px] text-amber-100/80">
              <Highlighted text={r.evidence.ocr} />
            </p>
          )}
        </div>
      )}
      {onFeedback && (
        <div className="mt-2 flex items-center justify-end gap-1 opacity-60 transition-opacity group-hover:opacity-100">
          <span className="mr-1 text-[11px] text-slate-500">Useful?</span>
          <button title="Relevant" className={clsx("btn btn-ghost px-2 py-1", feedback === "relevant" && "text-emerald-400")}
            onClick={(e) => { e.stopPropagation(); onFeedback("relevant"); }}><ThumbsUp size={13} /></button>
          <button title="Not relevant" className={clsx("btn btn-ghost px-2 py-1", feedback === "irrelevant" && "text-rose-400")}
            onClick={(e) => { e.stopPropagation(); onFeedback("irrelevant"); }}><ThumbsDown size={13} /></button>
        </div>
      )}
    </div>
  );
}
