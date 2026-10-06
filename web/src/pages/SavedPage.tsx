import { useEffect, useState } from "react";
import { BookmarkX, Bookmark as BookmarkIcon } from "lucide-react";
import { api, type Bookmark } from "../lib/api";
import { CONTENT_LABEL, fmtTime } from "../lib/format";
import Player from "../components/Player";

export default function SavedPage() {
  const [items, setItems] = useState<Bookmark[]>([]);
  const [sel, setSel] = useState<Bookmark | null>(null);
  const load = () => api.bookmarks().then((b) => { setItems(b); setSel((s) => s ?? b[0] ?? null); }).catch(() => undefined);
  useEffect(() => { load(); }, []);
  return (
    <div className="mx-auto grid max-w-[1400px] gap-5 px-4 pb-16 pt-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div>
        <h2 className="text-2xl font-bold text-white">Saved moments</h2>
        <p className="text-sm text-slate-400">Bookmarks pin the video, index version and exact timestamps, so reindexing never moves them.</p>
        <div className="mt-4 space-y-2">
          {items.length === 0 && <div className="card p-6 text-center text-sm text-slate-500"><BookmarkIcon className="mx-auto mb-2" />Nothing saved yet. Use “Save moment” under the player.</div>}
          {items.map((b) => (
            <div key={b.id} onClick={() => setSel(b)} className={`card flex cursor-pointer gap-3 p-3 hover:bg-ink-850 ${sel?.id === b.id ? "border-sky-500/50" : ""}`}>
              {b.thumb_url ? <img src={b.thumb_url} className="h-16 w-28 rounded-lg object-cover" alt="" /> : <div className="h-16 w-28 rounded-lg bg-ink-800" />}
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-semibold text-slate-100">{b.title || b.video_title}</div>
                <div className="truncate text-xs text-slate-400">{b.video_title} · {CONTENT_LABEL[b.content_type]}</div>
                <div className="mt-1 font-mono text-xs text-slate-500">{fmtTime(b.start_ms)} – {fmtTime(b.end_ms)} · index v{b.index_version}</div>
              </div>
              <button className="btn btn-ghost self-start px-2 hover:text-rose-300" title="Remove"
                onClick={async (e) => { e.stopPropagation(); await api.deleteBookmark(b.id); setSel(null); load(); }}><BookmarkX size={14} /></button>
            </div>
          ))}
        </div>
      </div>
      <div className="lg:sticky lg:top-20 lg:self-start">
        {sel && (
          <div className="card p-3">
            <div className="mb-2 px-1 font-semibold text-slate-100">{sel.title || sel.video_title}</div>
            <Player src={sel.playback_url} title={sel.video_title} durationMs={0} editable={false} poster={sel.thumb_url}
              interval={{ start_ms: sel.start_ms, end_ms: sel.end_ms }} />
          </div>
        )}
      </div>
    </div>
  );
}
