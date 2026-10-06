import { useEffect, useState } from "react";
import clsx from "clsx";
import { api } from "../lib/api";

export default function Health() {
  const [h, setH] = useState<Awaited<ReturnType<typeof api.health>> | null>(null);
  useEffect(() => {
    const load = () => api.health().then(setH).catch(() => setH(null));
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);
  const ml = h && typeof h.ml === "object" ? (h.ml.models_ready ? "ok" : "warming") : "down";
  const dots = [["API", h ? "ok" : "down"], ["DB", h?.db === "ok" ? "ok" : "down"], ["ML", ml]];
  return (
    <div className="hidden items-center gap-3 md:flex" title="Service health">
      {dots.map(([k, s]) => (
        <span key={k} className="flex items-center gap-1 text-[11px] text-slate-500">
          <span className={clsx("h-1.5 w-1.5 rounded-full", s === "ok" ? "bg-emerald-400" : s === "warming" ? "bg-amber-400" : "bg-rose-500")} />{k}
        </span>
      ))}
    </div>
  );
}
