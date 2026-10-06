import { useEffect, useState } from "react";
import { Bot, HelpCircle, Play, User } from "lucide-react";
import clsx from "clsx";
import type { SearchResult } from "../lib/api";
import { decorate, demoData, type DemoChat as Chat, type DemoData } from "../lib/demo";
import Player from "./Player";
import ResultCard from "./ResultCard";
import { DemoBanner } from "../pages/SearchPage";

/** Replays conversations recorded from the real LangGraph workflow (GitHub Pages demo). */
export default function DemoChat() {
  const [data, setData] = useState<DemoData | null>(null);
  const [chat, setChat] = useState(0);
  const [shown, setShown] = useState(1);
  const [sel, setSel] = useState<SearchResult | null>(null);
  useEffect(() => { demoData().then(setData).catch(() => undefined); }, []);
  if (!data) return null;
  const c: Chat = data.chats[chat];
  const turns = c.turns.slice(0, shown).map((t) => ({ ...t, results: t.response.results.map((r) => decorate(r, data)) }));
  const next = c.turns[shown];
  const pick = (i: number) => { setChat(i); setShown(1); setSel(null); };

  return (
    <div className="mx-auto max-w-[1400px] px-4 pb-10 pt-4">
      <DemoBanner />
      <div className="grid gap-5 lg:grid-cols-[220px_minmax(0,1fr)_minmax(0,1fr)]">
        <aside>
          <div className="label mb-2">Recorded conversations</div>
          {data.chats.map((x, i) => (
            <button key={i} onClick={() => pick(i)}
              className={clsx("mb-1 block w-full truncate rounded-lg px-2.5 py-1.5 text-left text-xs", i === chat ? "bg-ink-800 text-white" : "text-slate-400 hover:bg-ink-850")}>
              {x.title}
            </button>
          ))}
          <p className="mt-4 text-[11px] leading-relaxed text-slate-600">
            Each turn was answered by the real LangGraph workflow (rule-based parser, no LLM key) and saved. Follow-ups like
            “the second result” resolve against the list shown in the previous turn.
          </p>
        </aside>
        <section className="card space-y-4 p-4">
          {turns.map((t, i) => (
            <div key={i} className="space-y-2">
              <div className="flex justify-end gap-2.5">
                <div className="rounded-2xl rounded-br-sm bg-sky-500/15 px-3.5 py-2 text-sm text-sky-50">{t.user}</div>
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-sky-500/15"><User size={14} className="text-sky-300" /></div>
              </div>
              <div className="flex gap-2.5">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-violet-500/15"><Bot size={14} className="text-violet-300" /></div>
                <div className="flex-1">
                  <div className="text-sm text-slate-200">{t.response.message}</div>
                  <div className="mt-1 flex flex-wrap gap-1 text-[10.5px]">
                    <span className="rounded bg-ink-800 px-1.5 py-0.5 text-slate-400">parser: {t.response.interpretation?.parser}</span>
                    <span className="rounded bg-ink-800 px-1.5 py-0.5 text-slate-400">action: {t.response.interpretation?.action}</span>
                  </div>
                  {t.response.clarification && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {t.response.clarification.options.map((o) => (
                        <span key={o.value} className="btn btn-outline py-1 text-xs"><HelpCircle size={12} /> {o.label}</span>
                      ))}
                    </div>
                  )}
                  <div className="mt-2 space-y-1.5">
                    {t.results.slice(0, 4).map((r) => (
                      <ResultCard key={`${r.rank}-${r.start_ms}`} r={r} compact active={sel?.segment_id === r.segment_id && sel?.start_ms === r.start_ms} onSelect={() => setSel(r)} />
                    ))}
                  </div>
                </div>
              </div>
            </div>
          ))}
          {next ? (
            <button className="btn btn-primary" onClick={() => setShown((n) => n + 1)}>
              <Play size={14} /> Next turn: “{next.user}”
            </button>
          ) : (
            <div className="text-xs text-slate-500">End of this recorded conversation. Pick another on the left.</div>
          )}
        </section>
        <section className="lg:sticky lg:top-20 lg:self-start">
          {sel ? (
            <div className="card p-3">
              <div className="mb-2 truncate px-1 font-semibold text-slate-100">{sel.video_title}</div>
              <Player src={sel.playback_url} title={sel.video_title} durationMs={sel.video_duration_ms} editable={false}
                poster={sel.evidence.visual?.thumb_url ?? sel.thumb_url} offsetMs={sel.offset_ms ?? 0}
                interval={{ start_ms: sel.start_ms, end_ms: sel.end_ms }} />
            </div>
          ) : (
            <div className="card flex aspect-video items-center justify-center text-sm text-slate-600">Select a result to play it</div>
          )}
        </section>
      </div>
    </div>
  );
}
