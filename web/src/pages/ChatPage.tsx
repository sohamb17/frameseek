import { useEffect, useRef, useState } from "react";
import { Bot, HelpCircle, Loader2, MessageSquarePlus, Send, User } from "lucide-react";
import clsx from "clsx";
import { api, type SearchResult, type TurnResponse } from "../lib/api";
import { uid } from "../lib/format";
import Player from "../components/Player";
import ResultCard from "../components/ResultCard";
import { useToast } from "../components/Toast";

interface Msg { role: "user" | "assistant"; text: string; resp?: TurnResponse; turnId?: string }

const SUGGEST = [
  "where do they explain how replication works",
  "only the demos",
  "show more context around the second result",
  "find the same topic in another video",
  "within this video",
];

const KEY = "frameseek.conversation";
const store = {
  get: () => { try { return localStorage.getItem(KEY); } catch { return null; } },
  set: (v: string | null) => { try { if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch { /* private mode */ } },
};

export default function ChatPage() {
  const toast = useToast();
  const [conv, setConv] = useState<string | null>(store.get());
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<SearchResult | null>(null);
  const [list, setList] = useState<{ id: string; title: string }[]>([]);
  const end = useRef<HTMLDivElement>(null);

  const loadList = () => api.conversations().then(setList).catch(() => undefined);
  useEffect(() => { loadList(); }, []);

  // Reload a conversation from the server: its state lives in a durable LangGraph checkpoint.
  useEffect(() => {
    if (!conv) { setMsgs([]); return; }
    api.conversation(conv).then((c) => {
      const m: Msg[] = [];
      c.turns.forEach((t) => {
        m.push({ role: "user", text: t.user_text, turnId: t.client_turn_id });
        if (t.response && t.status === "done") m.push({ role: "assistant", text: t.response.message, resp: t.response });
      });
      setMsgs(m);
    }).catch(() => { store.set(null); setConv(null); });
  }, [conv]);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);

  const pendingClarify = (() => {
    const last = msgs[msgs.length - 1];
    return last?.resp?.status === "clarify" ? last.resp.clarification : undefined;
  })();

  const send = async (t: string, resume = false) => {
    if (!t.trim() || busy) return;
    setBusy(true);
    let id = conv;
    try {
      if (!id) {
        id = (await api.newConversation()).id;
        store.set(id);
        setConv(id);
      }
      const turnId = uid();
      setMsgs((m) => [...m, { role: "user", text: t, turnId }]);
      setText("");
      const r = await api.say(id, t, turnId, resume);
      setMsgs((m) => [...m, { role: "assistant", text: r.message, resp: r }]);
      if (r.results?.length) setSel(r.results[0]);
      loadList();
    } catch (e) {
      toast((e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const newChat = () => { store.set(null); setConv(null); setMsgs([]); setSel(null); };

  return (
    <div className="mx-auto grid max-w-[1400px] gap-5 px-4 pb-10 pt-6 lg:grid-cols-[220px_minmax(0,1fr)_minmax(0,1fr)]">
      <aside className="hidden lg:block">
        <button className="btn btn-outline w-full justify-center" onClick={newChat}><MessageSquarePlus size={14} /> New chat</button>
        <div className="mt-3 space-y-1">
          {list.map((c) => (
            <button key={c.id} onClick={() => { store.set(c.id); setConv(c.id); setSel(null); }}
              className={clsx("block w-full truncate rounded-lg px-2.5 py-1.5 text-left text-xs", c.id === conv ? "bg-ink-800 text-white" : "text-slate-400 hover:bg-ink-850")}>
              {c.title}
            </button>
          ))}
        </div>
        <p className="mt-4 text-[11px] leading-relaxed text-slate-600">
          Conversation state is checkpointed in Postgres by LangGraph. Reload the page or restart the ML service and
          “the second result” still points at the same clip.
        </p>
      </aside>

      <section className="card flex h-[calc(100vh-7.5rem)] flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {msgs.length === 0 && (
            <div className="mt-10 text-center">
              <Bot className="mx-auto text-violet-300" />
              <div className="mt-2 font-semibold text-slate-100">Conversational search</div>
              <p className="mx-auto mt-1 max-w-md text-sm text-slate-400">
                Ask for a moment, then refine it in plain language. References like “the second result” resolve against
                exactly the list you saw; ambiguous ones get a short clarifying question.
              </p>
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={clsx("flex gap-2.5", m.role === "user" && "justify-end")}>
              {m.role === "assistant" && <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-violet-500/15"><Bot size={14} className="text-violet-300" /></div>}
              <div className={clsx("max-w-[90%]", m.role === "user" ? "rounded-2xl rounded-br-sm bg-sky-500/15 px-3.5 py-2 text-sm text-sky-50" : "flex-1")}>
                {m.role === "user" ? m.text : (
                  <div>
                    {m.text && <div className="text-sm text-slate-200">{m.text}</div>}
                    {m.resp?.interpretation?.action && (
                      <div className="mt-1 flex flex-wrap gap-1 text-[10.5px]">
                        <span className="rounded bg-ink-800 px-1.5 py-0.5 text-slate-400">parser: {m.resp.interpretation.parser}</span>
                        <span className="rounded bg-ink-800 px-1.5 py-0.5 text-slate-400">action: {m.resp.interpretation.action}</span>
                        {m.resp.duplicate && <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-300">duplicate turn (replayed)</span>}
                      </div>
                    )}
                    {m.resp?.clarification && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {m.resp.clarification.options.map((o) => (
                          <button key={o.value} disabled={busy || m !== msgs[msgs.length - 1]} onClick={() => send(o.value, true)}
                            className="btn btn-outline py-1 text-xs"><HelpCircle size={12} /> {o.label}</button>
                        ))}
                      </div>
                    )}
                    {!!m.resp?.results?.length && (
                      <div className="mt-2 space-y-1.5">
                        {m.resp.results.slice(0, 5).map((r) => (
                          <ResultCard key={`${r.rank}-${r.start_ms}`} r={r} compact active={sel?.segment_id === r.segment_id && sel?.start_ms === r.start_ms}
                            onSelect={() => setSel(r)} />
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
              {m.role === "user" && <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-sky-500/15"><User size={14} className="text-sky-300" /></div>}
            </div>
          ))}
          {busy && <div className="flex items-center gap-2 text-xs text-slate-500"><Loader2 size={13} className="animate-spin" /> thinking…</div>}
          <div ref={end} />
        </div>
        <div className="border-t border-ink-800 p-3">
          <div className="mb-2 flex flex-wrap gap-1.5">
            {SUGGEST.map((s) => (
              <button key={s} onClick={() => send(s)} disabled={busy} className="rounded-full bg-ink-850 px-2.5 py-1 text-[11px] text-slate-400 ring-1 ring-ink-700 hover:text-white">{s}</button>
            ))}
          </div>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); send(text, !!pendingClarify); }}>
            <input className="input flex-1" placeholder={pendingClarify ? "Answer the question above, or ask something new" : "Ask for a moment…"}
              value={text} onChange={(e) => setText(e.target.value)} />
            <button className="btn btn-primary" disabled={busy || !text.trim()}><Send size={14} /></button>
          </form>
        </div>
      </section>

      <section className="lg:sticky lg:top-20 lg:self-start">
        {sel ? (
          <div className="card p-3">
            <div className="mb-2 truncate px-1 font-semibold text-slate-100">{sel.video_title}</div>
            <Player src={sel.playback_url} title={sel.video_title} durationMs={sel.video_duration_ms}
                      poster={sel.evidence.visual?.thumb_url ?? sel.thumb_url}
              interval={{ start_ms: sel.start_ms, end_ms: sel.end_ms }}
              onSave={async (iv) => {
                const r = await api.addBookmark({ video_id: sel.video_id, ...iv, title: sel.video_title, client_request_id: `${conv}:${sel.segment_id}:${iv.start_ms}:${iv.end_ms}` });
                toast(r.created ? "Moment saved" : "Already saved");
              }} />
          </div>
        ) : (
          <div className="card flex aspect-video items-center justify-center text-sm text-slate-600">Select a result to play it</div>
        )}
      </section>
    </div>
  );
}
