import { createContext, useCallback, useContext, useState } from "react";
import { CheckCircle2, AlertTriangle } from "lucide-react";

type Toast = { id: number; text: string; kind: "ok" | "err" };
const Ctx = createContext<(text: string, kind?: "ok" | "err") => void>(() => undefined);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, text, kind }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 3500);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className="card flex items-center gap-2 px-4 py-2.5 text-sm shadow-xl shadow-black/40">
            {t.kind === "ok" ? <CheckCircle2 size={16} className="text-emerald-400" /> : <AlertTriangle size={16} className="text-rose-400" />}
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
