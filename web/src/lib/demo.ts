// Static "recorded demo" mode for GitHub Pages (built with VITE_DEMO=1).
// Every response here was produced by the real FrameSeek pipeline and saved by
// scripts/export_demo.py; nothing is computed in the browser. Videos stream from their
// original public sources (FOSDEM, NASA), offset to the excerpt that was indexed.
import type { SearchResponse, SearchResult, TurnResponse, Video } from "./api";

export const DEMO = import.meta.env.VITE_DEMO === "1";
const BASE = import.meta.env.BASE_URL;

export interface DemoSearch { id: string; query: string; kind: string; arms: Record<string, string> }
export interface DemoChat { title: string; turns: { user: string; response: TurnResponse }[] }
export interface DemoData {
  meta: { created: string; repo_url: string; demo_video_url: string; note: string };
  videos: (Video & { source_url: string; offset_ms: number; source_page: string })[];
  searches: DemoSearch[];
  chats: DemoChat[];
}

let cache: Promise<DemoData> | null = null;
export function demoData(): Promise<DemoData> {
  cache ??= fetch(`${BASE}demo/data.json`).then((r) => r.json());
  return cache;
}

const abs = (u?: string | null) => (u && !u.startsWith("http") ? BASE + u : u ?? undefined);
const norm = (q: string) => q.toLowerCase().replace(/[^a-z0-9 ]/g, "").replace(/\s+/g, " ").trim();

export function decorate(r: SearchResult, d: DemoData): SearchResult {
  const v = d.videos.find((x) => x.id === r.video_id);
  return {
    ...r,
    playback_url: v?.source_url ?? "",
    offset_ms: v?.offset_ms ?? 0,
    thumb_url: abs(r.thumb_url),
    evidence: { ...r.evidence, visual: r.evidence.visual ? { ...r.evidence.visual, thumb_url: abs(r.evidence.visual.thumb_url) } : null },
  };
}

export async function demoSearch(query: string, ranker = "E"): Promise<SearchResponse> {
  const d = await demoData();
  const hit = d.searches.find((s) => norm(s.query) === norm(query));
  if (!hit) {
    throw new Error("This recorded demo only contains the example queries shown below. Run the Docker stack to search anything.");
  }
  const arm = hit.arms[ranker] ? ranker : "E";
  const resp: SearchResponse = await fetch(BASE + hit.arms[arm]).then((r) => r.json());
  return { ...resp, results: resp.results.map((r) => decorate(r, d)) };
}

export async function demoVideos(): Promise<Video[]> {
  const d = await demoData();
  return d.videos.map((v) => ({ ...v, poster_url: abs(v.poster_url), playback_url: v.source_url }));
}
