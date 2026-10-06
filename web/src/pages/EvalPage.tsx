import { useEffect, useState } from "react";
import { BarChart3, FlaskConical, Terminal } from "lucide-react";
import { api, type EvalReport } from "../lib/api";

const pct = (x: unknown) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "–");

function Bars({ rep, metric }: { rep: EvalReport; metric: string }) {
  const arms = Object.entries(rep.arms);
  return (
    <div className="space-y-2">
      {arms.map(([arm, a]) => {
        const v = (a.overall[metric] as number | undefined) ?? 0;
        const primary = arm === rep.primary_arm;
        return (
          <div key={arm} className="group flex items-center gap-3" title={`${arm} · ${a.label}: ${pct(v)} (${a.overall[metric.replace("success", "hits")] ?? "?"} of ${a.overall.n_answerable})`}>
            <div className="w-56 shrink-0 text-xs">
              <span className="mr-1.5 font-mono font-semibold text-slate-200">{arm}</span>
              <span className="text-slate-400">{a.label}</span>
            </div>
            <div className="relative h-5 flex-1 rounded bg-ink-850">
              <div className={`h-full rounded-r-[4px] ${primary ? "bg-sky-400" : "bg-sky-400/45"} group-hover:bg-sky-300`} style={{ width: `${v * 100}%` }} />
            </div>
            <div className="w-12 text-right font-mono text-xs text-slate-200">{pct(v)}</div>
          </div>
        );
      })}
    </div>
  );
}

export default function EvalPage() {
  const [rep, setRep] = useState<EvalReport | null>(null);
  const [metric, setMetric] = useState("success@5_iou0.3");
  useEffect(() => { api.evalReport().then(setRep).catch(() => undefined); }, []);

  if (!rep) return null;
  if (rep.available === false) {
    return (
      <div className="mx-auto max-w-3xl px-4 pt-10">
        <h2 className="text-2xl font-bold text-white">Evaluation</h2>
        <div className="card mt-4 space-y-3 p-5 text-sm text-slate-300">
          <div className="flex items-center gap-2 font-semibold"><FlaskConical size={16} className="text-violet-300" /> No report yet</div>
          <p className="text-slate-400">FrameSeek compares six retrieval arms on your human-labeled queries: keyword and semantic transcript search, transcript + on-screen text, visual only, fixed multimodal fusion, and the learned scorer.</p>
          <ol className="list-decimal space-y-1 pl-5 text-slate-400">
            <li>Write labeled queries in the <b>Label</b> tab (aim for 60+, across all five query types).</li>
            <li>Freeze splits by video group, train, then evaluate:</li>
          </ol>
          <pre className="overflow-x-auto rounded-lg bg-ink-950 p-3 font-mono text-xs text-slate-300"><Terminal size={12} className="mb-1" />
{`docker compose exec worker python -m frameseek.eval.labels freeze-splits
docker compose exec worker python -m frameseek.eval.train --activate
docker compose exec worker python -m frameseek.eval.evaluate --split dev`}</pre>
        </div>
      </div>
    );
  }

  const primary = rep.arms[rep.primary_arm];
  const keys = [["success@1_iou0.3", "success@1"], ["success@5_iou0.3", "success@5"], ["success@10_iou0.3", "success@10"], ["mrr_iou0.3", "MRR"], ["candidate_recall_iou0.3", "cand. recall"]];
  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 pb-16 pt-6">
      <div>
        <h2 className="text-2xl font-bold text-white">Evaluation <span className="text-base font-normal text-slate-500">· {rep.split} split</span></h2>
        <p className="text-sm text-slate-400">{rep.n_queries} labeled queries · {rep.corpus.videos} videos ({rep.corpus.hours} h) · {new Date(rep.created).toLocaleString()} · model {rep.model_id ?? "none"}</p>
      </div>
      {rep.n_queries < 60 && (
        <div className="card border-amber-500/30 p-3 text-xs text-amber-200/80">
          Pilot-sized label set: differences between arms are not reliable yet. Treat this as debugging output, not a result.
        </div>
      )}
      <div className="card p-5">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <BarChart3 size={16} className="text-sky-300" />
          <div className="font-semibold text-slate-100">Query success by retrieval arm</div>
          <select className="input ml-auto py-1 text-xs" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {["success@1_iou0.3", "success@5_iou0.3", "success@10_iou0.3", "success@5_iou0.5", "mrr_iou0.3", "candidate_recall_iou0.3"].map((m) => <option key={m}>{m}</option>)}
          </select>
        </div>
        <Bars rep={rep} metric={metric} />
        <p className="mt-3 text-[11px] text-slate-500">Success: a top-K result lies in a correct video and overlaps a labeled answer with temporal IoU ≥ 0.3. Arms E and F share the same candidate pool, so their difference isolates the learned combination.</p>
      </div>
      <div className="card overflow-x-auto p-5">
        <div className="mb-2 font-semibold text-slate-100">Table view</div>
        <table className="w-full text-xs">
          <thead><tr className="text-left text-slate-500"><th className="py-1">Arm</th>{keys.map(([, l]) => <th key={l}>{l}</th>)}<th>n</th><th>95% CI s@5 (grouped)</th><th>no-answer FP</th></tr></thead>
          <tbody>
            {Object.entries(rep.arms).map(([arm, a]) => (
              <tr key={arm} className="border-t border-ink-800">
                <td className="py-1.5"><span className="font-mono font-semibold">{arm}</span> <span className="text-slate-400">{a.label}</span></td>
                {keys.map(([k]) => <td key={k} className="font-mono text-slate-300">{pct(a.overall[k])}</td>)}
                <td className="font-mono text-slate-400">{a.overall.n_answerable}</td>
                <td className="font-mono text-slate-400">{Array.isArray(a.overall["success@5_iou0.3_ci95_grouped"]) ? (a.overall["success@5_iou0.3_ci95_grouped"] as number[]).map(pct).join(" – ") : "–"}</td>
                <td className="font-mono text-slate-400">{pct(a.overall["no_answer_false_positive_rate"])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        <div className="card p-5">
          <div className="mb-2 font-semibold text-slate-100">By query type · arm {rep.primary_arm}</div>
          <table className="w-full text-xs"><tbody>
            {Object.entries(primary.by_query_type).map(([t, s]) => (
              <tr key={t} className="border-t border-ink-800"><td className="py-1.5 text-slate-300">{t}</td><td className="font-mono">{pct(s["success@5_iou0.3"])}</td><td className="text-slate-500">n={s.n_answerable || s.n_no_answer}</td></tr>
            ))}
          </tbody></table>
          {rep.latency && (
            <>
              <div className="mb-2 mt-5 font-semibold text-slate-100">Search latency (cold query cache)</div>
              <table className="w-full text-xs"><tbody>
                {Object.entries(rep.latency).map(([arm, l]) => (
                  <tr key={arm} className="border-t border-ink-800"><td className="py-1.5 font-mono">{arm}</td><td>p50 {l.p50_ms} ms</td><td>p95 {l.p95_ms} ms</td><td className="text-slate-500">embed {l.embed_p50_ms} · retrieval {l.retrieval_p50_ms}</td></tr>
                ))}
              </tbody></table>
            </>
          )}
        </div>
        <div className="card p-5">
          <div className="mb-2 font-semibold text-slate-100">Failure gallery · arm {rep.primary_arm}</div>
          <div className="max-h-80 space-y-1 overflow-y-auto">
            {rep.failures.length === 0 && <div className="text-xs text-slate-500">No misses at success@5.</div>}
            {rep.failures.map((f) => (
              <div key={f.id} className="flex gap-2 text-xs"><span className="shrink-0 rounded bg-rose-500/10 px-1.5 text-rose-300">{f.category}</span><span className="text-slate-500">{f.type}</span><span className="text-slate-300">{f.query}</span></div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
