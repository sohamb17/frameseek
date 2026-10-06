import { Brain } from "lucide-react";
import type { SearchResult } from "../lib/api";
import { fmtTime } from "../lib/format";

const CH_LABEL: Record<string, string> = {
  transcript_lex: "Speech · keywords",
  transcript_sem: "Speech · meaning",
  ocr_lex: "Screen text · keywords",
  ocr_sem: "Screen text · meaning",
  visual: "Visual · CLIP frame match",
};
const CH_COLOR: Record<string, string> = {
  transcript_lex: "text-speech", transcript_sem: "text-speech", ocr_lex: "text-screen", ocr_sem: "text-screen", visual: "text-visual",
};

/** Explains a result: which retrieval channels found it, their raw scores/ranks, and (for the learned scorer) top feature contributions. */
export default function WhyPanel({ r }: { r: SearchResult }) {
  return (
    <div className="card p-4">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate-200"><Brain size={15} className="text-violet-300" /> Why this result</div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-slate-500">
            <th className="py-1 font-medium">Channel</th><th className="font-medium">Raw score</th><th className="font-medium">Channel rank</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(r.channel_scores).map(([ch, v]) => (
            <tr key={ch} className="border-t border-ink-800">
              <td className={`py-1.5 ${CH_COLOR[ch]}`}>{CH_LABEL[ch] ?? ch}</td>
              <td className="font-mono text-slate-300">{v.score.toFixed(3)}</td>
              <td className="font-mono text-slate-400">{v.rank ? `#${v.rank}` : <span className="text-slate-600">not retrieved</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {r.evidence.visual && (
        <div className="mt-3 flex items-center gap-3 rounded-lg bg-ink-850 p-2">
          {r.evidence.visual.thumb_url && <img src={r.evidence.visual.thumb_url} className="h-14 rounded ring-1 ring-visual/40" alt="best matching frame" />}
          <div className="text-xs text-slate-400">
            Best matching frame at <span className="font-mono text-slate-200">{fmtTime(r.evidence.visual.ts_ms)}</span>
            <br />CLIP similarity <span className="font-mono text-visual">{r.evidence.visual.similarity.toFixed(3)}</span>
          </div>
        </div>
      )}
      {r.explain && (
        <div className="mt-3">
          <div className="label mb-1">Learned scorer · top feature contributions</div>
          {r.explain.map((e) => (
            <div key={e.feature} className="flex items-center gap-2 text-xs">
              <span className="w-36 truncate font-mono text-slate-400">{e.feature}</span>
              <div className="h-1.5 flex-1 rounded bg-ink-800">
                <div className={`h-full rounded ${e.contribution >= 0 ? "bg-emerald-400" : "bg-rose-400"}`}
                  style={{ width: `${Math.min(100, Math.abs(e.contribution) * 30)}%` }} />
              </div>
              <span className="w-12 text-right font-mono text-slate-300">{e.contribution > 0 ? "+" : ""}{e.contribution.toFixed(2)}</span>
            </div>
          ))}
        </div>
      )}
      <p className="mt-3 text-[11px] leading-relaxed text-slate-600">
        Raw scores come from different models and are not comparable across channels; ranking uses their ranks and
        query-relative values. Scores are relevance scores, not calibrated confidence.
      </p>
    </div>
  );
}
