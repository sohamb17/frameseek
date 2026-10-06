import { useEffect, useMemo, useState } from "react";
import { Eye, Gauge, Loader2, Mic, ScanText, Search, Sparkles, Zap } from "lucide-react";
import clsx from "clsx";
import { api, type ContentType, type SearchResponse, type SearchResult, type Video } from "../lib/api";
import Player from "../components/Player";
import ResultCard from "../components/ResultCard";
import WhyPanel from "../components/WhyPanel";
import { useToast } from "../components/Toast";

const EXAMPLES = [
  { q: "why does replication lag between regions", kind: "speech" },
  { q: "tiup dm deploy command", kind: "screen text" },
  { q: "circuit boards wired to a multimeter", kind: "visual" },
  { q: "guess the number game running in the terminal", kind: "mixed" },
  { q: "astronaut demonstrates a lever", kind: "visual + speech" },
];

const RANKERS = [
  { v: "auto", label: "Auto (best available)" },
  { v: "A", label: "A · transcript keywords" },
  { v: "B", label: "B · transcript semantic" },
  { v: "C", label: "C · transcript + screen text" },
  { v: "D", label: "D · visual only" },
  { v: "E", label: "E · all, fixed fusion" },
  { v: "F", label: "F · all, learned scorer" },
];

const TYPES: { v: ContentType; label: string }[] = [
  { v: "talk", label: "Talks" }, { v: "screencast", label: "Screencasts" }, { v: "demo", label: "Demos" },
];

function Hero() {
  const items = [
    { icon: Mic, color: "text-speech", ring: "ring-speech/30", title: "What is said", body: "Whisper transcribes speech with word-level timestamps." },
    { icon: ScanText, color: "text-screen", ring: "ring-screen/30", title: "What is written", body: "OCR reads slides, terminals and code on screen." },
    { icon: Eye, color: "text-visual", ring: "ring-visual/30", title: "What is shown", body: "CLIP matches sampled frames to your words." },
  ];
  return (
    <div className="mx-auto mt-6 grid max-w-4xl gap-3 sm:grid-cols-3">
      {items.map((it) => (
        <div key={it.title} className={clsx("card p-4 ring-1", it.ring)}>
          <it.icon className={it.color} size={20} />
          <div className="mt-2 font-semibold text-slate-100">{it.title}</div>
          <div className="mt-1 text-sm text-slate-400">{it.body}</div>
        </div>
      ))}
      <div className="sm:col-span-3 flex items-center justify-center gap-2 text-sm text-slate-400">
        <Sparkles size={15} className="text-violet-300" />
        Every 20-second window is scored on all three, then fused into one ranked list of timestamped moments.
      </div>
    </div>
  );
}

export default function SearchPage() {
  const toast = useToast();
  const [videos, setVideos] = useState<Video[]>([]);
  const [q, setQ] = useState("");
  const [types, setTypes] = useState<ContentType[]>([]);
  const [videoId, setVideoId] = useState("");
  const [ranker, setRanker] = useState("auto");
  const [loading, setLoading] = useState(false);
  const [resp, setResp] = useState<SearchResponse | null>(null);
  const [sel, setSel] = useState<SearchResult | null>(null);
  const [fb, setFb] = useState<Record<number, "relevant" | "irrelevant">>({});

  useEffect(() => { api.videos().then(setVideos).catch(() => undefined); }, []);
  const ready = videos.filter((v) => v.active_index_version != null);

  const run = async (query = q) => {
    if (!query.trim()) return;
    setQ(query);
    setLoading(true);
    try {
      const r = await api.search({ query, content_types: types.length ? types : undefined,
        video_ids: videoId ? [videoId] : undefined, ranker, k: 10 });
      setResp(r);
      setSel(r.results[0] ?? null);
      setFb({});
    } catch (e) {
      toast(`Search failed: ${(e as Error).message}`, "err");
    } finally {
      setLoading(false);
    }
  };

  const markers = useMemo(() => (resp && sel ? resp.results.filter((r) => r.video_id === sel.video_id && r.rank !== sel.rank) : []), [resp, sel]);

  const feedback = async (r: SearchResult, label: "relevant" | "irrelevant") => {
    if (!resp) return;
    try {
      await api.feedback(resp.search_id, r.rank, label);
      setFb((x) => ({ ...x, [r.rank]: label }));
      toast("Feedback recorded for the review set");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  const save = async (r: SearchResult, iv: { start_ms: number; end_ms: number }) => {
    try {
      const res = await api.addBookmark({
        video_id: r.video_id, start_ms: iv.start_ms, end_ms: iv.end_ms, title: resp?.query ?? r.video_title,
        query: resp?.query, search_id: resp?.search_id,
        client_request_id: `${resp?.search_id}:${r.rank}:${iv.start_ms}:${iv.end_ms}`,
      });
      toast(res.created ? "Moment saved" : "Already saved");
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  return (
    <div className="mx-auto max-w-[1400px] px-4 pb-16">
      <div className={clsx("transition-all", resp ? "pt-4" : "pt-16 text-center")}>
        {!resp && (
          <>
            <h1 className="text-4xl font-bold tracking-tight text-white sm:text-5xl">
              Find the <span className="gradient-text">moment</span>, not just the video.
            </h1>
            <p className="mx-auto mt-3 max-w-2xl text-slate-400">
              Search inside a library of talks, screencasts and demos by what is said, what is written on screen,
              and what is shown. Every result is a timestamped clip with the evidence that matched.
            </p>
          </>
        )}
        <form onSubmit={(e) => { e.preventDefault(); run(); }} className={clsx("mx-auto flex max-w-3xl gap-2", resp ? "max-w-none" : "mt-8")}>
          <div className="relative flex-1">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-500" size={18} />
            <input className="input h-12 w-full pl-10 text-base" placeholder="e.g. where do they show the cable plugged into the board"
              value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          </div>
          <button className="btn btn-primary h-12 px-5" disabled={loading || !q.trim()}>
            {loading ? <Loader2 className="animate-spin" size={16} /> : <Zap size={16} />} Search
          </button>
        </form>

        <div className={clsx("mt-3 flex flex-wrap items-center gap-2 text-sm", !resp && "justify-center")}>
          {TYPES.map((t) => (
            <button key={t.v} onClick={() => setTypes((x) => (x.includes(t.v) ? x.filter((y) => y !== t.v) : [...x, t.v]))}
              className={clsx("rounded-full px-3 py-1 text-xs ring-1 transition-colors",
                types.includes(t.v) ? "bg-sky-500/15 text-sky-300 ring-sky-500/40" : "text-slate-400 ring-ink-700 hover:text-slate-200")}>
              {t.label}
            </button>
          ))}
          <select className="input py-1 text-xs" value={videoId} onChange={(e) => setVideoId(e.target.value)}>
            <option value="">All {ready.length} ready videos</option>
            {ready.map((v) => <option key={v.id} value={v.id}>{v.title}</option>)}
          </select>
          <select className="input py-1 text-xs" value={ranker} onChange={(e) => setRanker(e.target.value)} title="Retrieval arm (for comparing baselines)">
            {RANKERS.map((r) => <option key={r.v} value={r.v}>{r.label}</option>)}
          </select>
        </div>

        {!resp && (
          <>
            <div className="mt-5 flex flex-wrap justify-center gap-2">
              {EXAMPLES.map((ex) => (
                <button key={ex.q} onClick={() => run(ex.q)} className="rounded-full bg-ink-850 px-3 py-1.5 text-xs text-slate-300 ring-1 ring-ink-700 hover:bg-ink-800 hover:text-white">
                  {ex.q} <span className="ml-1 text-slate-500">· {ex.kind}</span>
                </button>
              ))}
            </div>
            {ready.length === 0 && (
              <p className="mt-6 text-sm text-amber-300/80">No videos are indexed yet. Add some in the Library tab first.</p>
            )}
            <Hero />
          </>
        )}
      </div>

      {resp && (
        <>
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
            <span className="rounded-md bg-violet-500/10 px-2 py-0.5 text-violet-300 ring-1 ring-violet-500/30">Arm {resp.arm} · {resp.arm_label}</span>
            <span><Gauge size={12} className="mr-1 inline" />{Math.round(resp.timings.total_ms)} ms
              <span className="text-slate-600"> (embed {Math.round(resp.timings.embed_ms)} · retrieval {Math.round((resp.timings.candidates_ms ?? 0) + (resp.timings.features_ms ?? 0))} · rank {Math.round(resp.timings.rank_ms ?? 0)})</span></span>
            <span>{resp.videos_searched} videos · {resp.candidate_pool} candidate windows from 5 channels</span>
            {resp.model_version && <span>model {resp.model_version}</span>}
          </div>
          <div className="mt-3 grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
            <div className="flex flex-col gap-2.5">
              {resp.results.length === 0 && <div className="card p-6 text-center text-slate-400">No matching moments. Try other words or remove filters.</div>}
              {resp.results.map((r) => (
                <ResultCard key={r.rank} r={r} active={sel?.rank === r.rank} onSelect={() => setSel(r)}
                  feedback={fb[r.rank]} onFeedback={(l) => feedback(r, l)} />
              ))}
            </div>
            <div className="lg:sticky lg:top-20 lg:self-start">
              {sel ? (
                <div className="flex flex-col gap-3">
                  <div className="card p-3">
                    <div className="mb-2 flex items-baseline justify-between gap-2 px-1">
                      <div className="truncate font-semibold text-slate-100">{sel.video_title}</div>
                      <div className="shrink-0 text-xs text-slate-500">result #{sel.rank}</div>
                    </div>
                    <Player src={sel.playback_url} title={sel.video_title} durationMs={sel.video_duration_ms}
                      poster={sel.evidence.visual?.thumb_url ?? sel.thumb_url}
                      interval={{ start_ms: sel.start_ms, end_ms: sel.end_ms }} markers={markers}
                      onSave={(iv) => save(sel, iv)} />
                  </div>
                  <WhyPanel r={sel} />
                </div>
              ) : null}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
