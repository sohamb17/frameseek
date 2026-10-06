import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, Clock, Film, Link2, Loader2, RefreshCw, Trash2, UploadCloud, X } from "lucide-react";
import clsx from "clsx";
import { api, type ContentType, type Video, type VideoDetail } from "../lib/api";
import { CONTENT_LABEL, fmtBytes, fmtTime } from "../lib/format";
import { usePoll } from "../lib/route";
import { useToast } from "../components/Toast";

const STAGES = ["queued", "extracting", "transcribing", "embedding", "indexing", "ready"] as const;
const STAGE_HELP: Record<string, string> = {
  queued: "Waiting for a worker",
  extracting: "Probing streams, preparing a browser-safe copy, sampling frames and audio",
  transcribing: "Speech recognition (Whisper) and on-screen text (OCR)",
  embedding: "Building 20 s windows and text/visual embeddings",
  indexing: "Writing the new index version and publishing it atomically",
  ready: "Searchable",
};

function Pipeline({ v }: { v: Video }) {
  const running = v.job_status === "running" || v.job_status === "queued";
  const current = v.status === "ready" && !running ? "ready" : v.job_stage ?? "queued";
  const idx = STAGES.indexOf(current as (typeof STAGES)[number]);
  if (v.status === "importing") return <div className="flex items-center gap-1.5 text-xs text-sky-300"><Loader2 size={12} className="animate-spin" /> Downloading from URL…</div>;
  if (v.status === "failed") return (
    <div className="flex items-start gap-1.5 text-xs text-rose-300"><AlertTriangle size={13} className="mt-0.5 shrink-0" /><span className="line-clamp-2">{v.error ?? v.job_error}</span></div>
  );
  return (
    <div>
      <div className="flex gap-1">
        {STAGES.map((s, i) => (
          <div key={s} title={`${s}: ${STAGE_HELP[s]}`} className="h-1.5 flex-1 overflow-hidden rounded-full bg-ink-800">
            <div className={clsx("h-full rounded-full", i < idx || current === "ready" ? "bg-emerald-400/80" : i === idx ? "bg-sky-400" : "")}
              style={{ width: i < idx || current === "ready" ? "100%" : i === idx ? `${Math.max(8, (v.job_progress ?? 0) * 100)}%` : "0%" }} />
          </div>
        ))}
      </div>
      <div className="mt-1.5 flex items-center gap-1.5 text-xs">
        {current === "ready" ? <CheckCircle2 size={12} className="text-emerald-400" /> : <Loader2 size={12} className="animate-spin text-sky-400" />}
        <span className={current === "ready" ? "text-emerald-300" : "text-sky-300"}>{current}</span>
        {current !== "ready" && <span className="text-slate-500">· {STAGE_HELP[current]}</span>}
        {(v.job_attempt ?? 0) > 1 && running && <span className="text-amber-300">· attempt {v.job_attempt}</span>}
        {v.status === "ready" && running && <span className="text-slate-500">· v{v.active_index_version} stays searchable while v{v.job_index_version} builds</span>}
      </div>
    </div>
  );
}

function Uploader({ onDone }: { onDone: () => void }) {
  const toast = useToast();
  const [ctype, setCtype] = useState<ContentType>("talk");
  const [uploads, setUploads] = useState<{ name: string; p: number }[]>([]);
  const [url, setUrl] = useState("");
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const send = async (files: FileList | null) => {
    if (!files) return;
    for (const f of Array.from(files)) {
      setUploads((u) => [...u, { name: f.name, p: 0 }]);
      try {
        const r = await api.upload(f, { content_type: ctype, title: f.name.replace(/\.[^.]+$/, "") }, (p) =>
          setUploads((u) => u.map((x) => (x.name === f.name ? { ...x, p } : x))));
        toast(r.deduplicated ? `${f.name} is already in the library` : `${f.name} uploaded, processing started`);
      } catch (e) {
        toast(`${f.name}: ${(e as Error).message}`, "err");
      } finally {
        setUploads((u) => u.filter((x) => x.name !== f.name));
        onDone();
      }
    }
  };

  const importUrl = async () => {
    try {
      await api.importUrl({ url, content_type: ctype });
      setUrl("");
      toast("Import started");
      onDone();
    } catch (e) {
      toast((e as Error).message, "err");
    }
  };

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="label">Content type</span>
        {(["talk", "screencast", "demo", "other"] as ContentType[]).map((t) => (
          <button key={t} onClick={() => setCtype(t)} className={clsx("rounded-full px-3 py-1 text-xs ring-1",
            ctype === t ? "bg-sky-500/15 text-sky-300 ring-sky-500/40" : "text-slate-400 ring-ink-700")}>{CONTENT_LABEL[t]}</button>
        ))}
        <span className="text-xs text-slate-600">Used by filters such as “only the demos”.</span>
      </div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        className={clsx("mt-3 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-7 text-center transition-colors",
          drag ? "border-sky-400 bg-sky-500/5" : "border-ink-700 hover:border-ink-700 hover:bg-ink-850/60")}
      >
        <UploadCloud className="text-sky-300" />
        <div className="mt-2 text-sm text-slate-200">Drop videos here or click to choose</div>
        <div className="mt-1 text-xs text-slate-500">MP4, MOV, MKV or WebM · up to 60 min / 2 GB · only recordings you are permitted to process</div>
        <input ref={input} type="file" multiple accept="video/*,.mkv,.webm,.mov" className="hidden" onChange={(e) => send(e.target.files)} />
      </div>
      {uploads.map((u) => (
        <div key={u.name} className="mt-2 flex items-center gap-2 text-xs text-slate-400">
          <span className="w-48 truncate">{u.name}</span>
          <div className="h-1.5 flex-1 rounded bg-ink-800"><div className="h-full rounded bg-sky-400" style={{ width: `${u.p * 100}%` }} /></div>
          <span className="w-10 text-right">{Math.round(u.p * 100)}%</span>
        </div>
      ))}
      <div className="mt-3 flex gap-2">
        <div className="relative flex-1">
          <Link2 size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input className="input w-full pl-9" placeholder="…or a direct https:// link to a media file" value={url} onChange={(e) => setUrl(e.target.value)} />
        </div>
        <button className="btn btn-outline" disabled={!url.startsWith("https://")} onClick={importUrl}>Import</button>
      </div>
    </div>
  );
}

function Details({ id, onClose }: { id: string; onClose: () => void }) {
  const [d, setD] = useState<VideoDetail | null>(null);
  useEffect(() => { api.video(id).then(setD).catch(() => undefined); }, [id]);
  if (!d) return null;
  const active = d.manifests.filter((m) => m.index_version === d.active_index_version);
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={onClose}>
      <div className="h-full w-full max-w-xl overflow-y-auto border-l border-ink-800 bg-ink-900 p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-white">{d.title}</div>
            <div className="mt-1 text-xs text-slate-500">{d.attribution}</div>
            {d.license && <div className="text-xs text-slate-500">License: {d.license}</div>}
          </div>
          <button className="btn btn-ghost" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
          <Info k="Duration" v={fmtTime(d.duration_ms)} />
          <Info k="Resolution" v={d.width ? `${d.width}×${d.height}` : "-"} />
          <Info k="Size" v={fmtBytes(d.size_bytes)} />
          <Info k="Audio" v={d.has_audio == null ? "-" : d.has_audio ? "yes" : "no audio track"} />
          <Info k="Original codecs" v={d.probe?.original ? `${d.probe.original.video_codec} / ${d.probe.original.audio_codec ?? "none"} (${d.probe.original.format})` : "-"} />
          <Info k="Playback" v={d.probe ? (d.probe.derived_playback ? "derived H.264/AAC MP4" : "original file") : "-"} />
        </div>
        <div className="label mt-5">Index versions</div>
        <div className="mt-1 space-y-1">
          {d.versions.map((v) => (
            <div key={v.version} className="flex items-center gap-2 text-xs">
              <span className="font-mono text-slate-300">v{v.version}</span>
              <span className={clsx("rounded px-1.5", v.status === "ready" ? "bg-emerald-500/15 text-emerald-300" : v.status === "failed" ? "bg-rose-500/15 text-rose-300" : "bg-ink-800 text-slate-400")}>{v.status}</span>
              <span className="font-mono text-slate-600">config {v.config_hash.slice(0, 10)}</span>
            </div>
          ))}
        </div>
        <div className="label mt-5">Stage manifests (active version)</div>
        <div className="mt-1 space-y-1.5">
          {active.map((m) => (
            <div key={m.stage} className="rounded-lg bg-ink-850 p-2 text-xs">
              <div className="flex justify-between"><span className="font-semibold text-slate-200">{m.stage}</span>
                <span className="font-mono text-slate-400">{String(m.metrics.wall_seconds ?? "")} s · attempt {m.attempt}</span></div>
              <div className="mt-1 font-mono text-[10.5px] leading-relaxed text-slate-500">
                {Object.entries(m.metrics).filter(([k]) => !["wall_seconds", "probe", "original", "playback"].includes(k)).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ")}
              </div>
            </div>
          ))}
          {active[0] && (
            <div className="mt-2 rounded-lg bg-ink-850 p-2 font-mono text-[10.5px] text-slate-500">
              {Object.entries(active[0].versions).map(([k, v]) => <div key={k}>{k}: {v}</div>)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const Info = ({ k, v }: { k: string; v: string }) => (
  <div className="rounded-lg bg-ink-850 p-2"><div className="text-slate-500">{k}</div><div className="mt-0.5 text-slate-200">{v}</div></div>
);

export default function LibraryPage() {
  const toast = useToast();
  const [videos, setVideos] = useState<Video[]>([]);
  const [detail, setDetail] = useState<string | null>(null);
  const load = useCallback(() => { api.videos().then(setVideos).catch(() => undefined); }, []);
  useEffect(load, [load]);
  const busy = videos.some((v) => ["importing", "queued", "processing", "registered"].includes(v.status) || v.job_status === "running" || v.job_status === "queued");
  usePoll(load, 2000, busy);

  const totalMs = videos.reduce((a, v) => a + (v.duration_ms ?? 0), 0);
  return (
    <div className="mx-auto max-w-6xl px-4 pb-16 pt-6">
      <div className="mb-4 flex items-end justify-between">
        <div>
          <h2 className="text-2xl font-bold text-white">Library</h2>
          <p className="text-sm text-slate-400">{videos.length} videos · {fmtTime(totalMs)} total · ingestion runs in a background worker with resumable stages</p>
        </div>
      </div>
      <Uploader onDone={load} />
      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {videos.map((v) => (
          <div key={v.id} className="card overflow-hidden">
            <div className="relative aspect-video bg-ink-850">
              {v.poster_url ? <img src={v.poster_url} className="h-full w-full object-cover" alt="" /> :
                <div className="flex h-full items-center justify-center text-slate-600"><Film /></div>}
              <span className="absolute left-2 top-2 rounded-full bg-black/70 px-2 py-0.5 text-[11px] text-slate-200">{CONTENT_LABEL[v.content_type]}</span>
              {v.duration_ms != null && <span className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 font-mono text-[11px] text-white"><Clock size={10} className="mr-1 inline" />{fmtTime(v.duration_ms)}</span>}
            </div>
            <div className="p-3">
              <div className="line-clamp-1 font-semibold text-slate-100" title={v.title}>{v.title}</div>
              <div className="mt-2"><Pipeline v={v} /></div>
              <div className="mt-3 flex items-center gap-1">
                <button className="btn btn-ghost px-2 text-xs" onClick={() => setDetail(v.id)}>Details <ChevronRight size={12} /></button>
                <button className="btn btn-ghost ml-auto px-2" title="Reindex (builds a new version; the current one stays searchable)"
                  onClick={async () => { try { await api.process(v.id, true); toast("Reindex queued"); load(); } catch (e) { toast((e as Error).message, "err"); } }}>
                  <RefreshCw size={14} />
                </button>
                <button className="btn btn-ghost px-2 hover:text-rose-300" title="Delete"
                  onClick={async () => { if (confirm(`Delete "${v.title}"?`)) { await api.deleteVideo(v.id); load(); } }}>
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
      {detail && <Details id={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}
