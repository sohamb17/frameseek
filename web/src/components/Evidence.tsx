import { Eye, Mic, ScanText } from "lucide-react";
import clsx from "clsx";
import type { Modality } from "../lib/api";

export const MODALITY: Record<Modality, { label: string; icon: typeof Mic; text: string; bg: string; bar: string; ring: string }> = {
  speech: { label: "Speech", icon: Mic, text: "text-speech", bg: "bg-speech/10", bar: "bg-speech", ring: "ring-speech/30" },
  screen_text: { label: "On-screen text", icon: ScanText, text: "text-screen", bg: "bg-screen/10", bar: "bg-screen", ring: "ring-screen/30" },
  visual: { label: "Visual", icon: Eye, text: "text-visual", bg: "bg-visual/10", bar: "bg-visual", ring: "ring-visual/30" },
};

export function ModalityChip({ m, dim = false }: { m: Modality; dim?: boolean }) {
  const meta = MODALITY[m];
  const Icon = meta.icon;
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1",
      dim ? "bg-ink-800 text-slate-500 ring-ink-700" : [meta.bg, meta.text, meta.ring])}>
      <Icon size={12} /> {meta.label}
    </span>
  );
}

/** Three small bars: how strongly this result matched in each modality, relative to this query's other candidates. */
export function StrengthBars({ s }: { s: Partial<Record<Modality, number>> }) {
  return (
    <div className="flex flex-col gap-1 w-28" title="Match strength per modality (percentile among this query's candidates)">
      {(Object.keys(MODALITY) as Modality[]).map((m) => (
        <div key={m} className="flex items-center gap-1.5">
          {(() => { const I = MODALITY[m].icon; return <I size={11} className={MODALITY[m].text} />; })()}
          <div className="h-1.5 flex-1 rounded-full bg-ink-800 overflow-hidden">
            <div className={clsx("h-full rounded-full", MODALITY[m].bar)} style={{ width: `${Math.round((s[m] ?? 0) * 100)}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Renders text where Postgres ts_headline marked matches as [[word]]. */
export function Highlighted({ text, className }: { text: string; className?: string }) {
  const parts = text.split(/(\[\[.*?\]\])/g);
  return (
    <span className={className}>
      {parts.map((p, i) => (p.startsWith("[[") ? <mark key={i}>{p.slice(2, -2)}</mark> : <span key={i}>{p}</span>))}
    </span>
  );
}
