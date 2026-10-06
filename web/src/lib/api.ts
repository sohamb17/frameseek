import { DEMO, demoSearch, demoVideos } from "./demo";

// Typed client for the Go API. Every call goes through /api; media links come back pre-signed.

export type ContentType = "talk" | "screencast" | "demo" | "other";
export type Modality = "speech" | "screen_text" | "visual";

export interface Video {
  id: string;
  collection_id: string;
  title: string;
  content_type: ContentType;
  source_kind: "upload" | "url";
  source_url: string | null;
  license: string | null;
  attribution: string | null;
  status: "registered" | "importing" | "queued" | "processing" | "ready" | "failed";
  error: string | null;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  has_audio: boolean | null;
  active_index_version: number | null;
  size_bytes: number | null;
  created_at: string;
  poster_url?: string;
  playback_url?: string;
  /** Static demo only. */
  source_page?: string;
  offset_ms?: number;
  job_id: string | null;
  job_status: string | null;
  job_stage: string | null;
  job_progress: number | null;
  job_attempt: number | null;
  job_error: string | null;
  job_index_version: number | null;
}

export interface Manifest {
  index_version: number;
  stage: string;
  attempt: number;
  versions: Record<string, string>;
  metrics: Record<string, unknown>;
  started_at: string;
  finished_at: string;
}

export interface VideoDetail extends Video {
  versions: { version: number; status: string; config_hash: string; created_at: string; published_at: string | null }[];
  jobs: { id: string; status: string; stage: string; attempt: number; index_version: number; error: string | null; created_at: string }[];
  manifests: Manifest[];
  probe: { original?: Record<string, unknown>; playback?: Record<string, unknown>; derived_playback?: boolean } | null;
}

export interface ChannelScore { score: number; rank: number | null }

export interface SearchResult {
  rank: number;
  video_id: string;
  video_title: string;
  content_type: ContentType;
  video_duration_ms: number;
  index_version: number;
  segment_id: number;
  start_ms: number;
  end_ms: number;
  score: number;
  evidence_types: Modality[];
  retrieved_by: string[];
  modality_strength: Partial<Record<Modality, number>>;
  evidence: {
    transcript: string;
    transcript_missing: boolean;
    ocr: string;
    ocr_missing: boolean;
    visual: { frame_id: number; ts_ms: number; similarity: number; thumb_url?: string } | null;
  };
  thumb_url?: string;
  playback_url: string;
  channel_scores: Record<string, ChannelScore>;
  explain: { feature: string; contribution: number }[] | null;
  /** Static demo only: position of this video's excerpt inside the original source file. */
  offset_ms?: number;
  low_relevance?: boolean;
  expanded_from?: { start_ms: number; end_ms: number };
  search_id?: string;
}

export interface SearchResponse {
  search_id: string;
  query: string;
  arm: string;
  arm_label: string;
  model_version: string | null;
  results: SearchResult[];
  timings: Record<string, number>;
  candidate_counts: Record<string, number>;
  candidate_pool: number;
  videos_searched: number;
}

export interface SearchParams {
  query: string;
  content_types?: ContentType[];
  video_ids?: string[];
  ranker?: string;
  k?: number;
}

export interface Bookmark {
  id: string;
  video_id: string;
  video_title: string;
  content_type: ContentType;
  index_version: number | null;
  start_ms: number;
  end_ms: number;
  title: string;
  note: string;
  query: string | null;
  created_at: string;
  playback_url: string;
  thumb_url?: string;
}

export interface Clarification { kind: string; question: string; options: { label: string; value: string }[] }

export interface TurnResponse {
  status: "ok" | "clarify" | "stale";
  message: string;
  results: SearchResult[];
  clarification?: Clarification;
  interpretation?: { parser?: string; action?: string; query?: string | null; filters?: Record<string, unknown> | null };
  state?: { query: string | null; filters: Record<string, unknown> };
  duplicate?: boolean;
  seq?: number;
}

export interface ConversationTurn {
  client_turn_id: string;
  seq: number;
  user_text: string;
  status: string;
  response: TurnResponse | null;
}

export interface EvalQuery {
  id: string;
  query: string;
  query_type: string;
  notes: string;
  created_at: string;
  answers: { video_id: string; video_title: string; start_ms: number; end_ms: number }[];
}

export interface TimelineSegment {
  id: number;
  idx: number;
  start_ms: number;
  end_ms: number;
  transcript: string;
  ocr_text: string;
  thumb_url?: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).error ?? msg;
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const json = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

const liveApi = {
  health: () => req<{ api: string; db: string; ml: { ok: boolean; models_ready: boolean } | string }>("/healthz"),
  me: () => req<{ owner_id: string; default_collection_id: string; limits: { max_upload_bytes: number } }>("/api/me"),
  videos: () => req<Video[]>("/api/videos"),
  video: (id: string) => req<VideoDetail>(`/api/videos/${id}`),
  timeline: (id: string) => req<TimelineSegment[]>(`/api/videos/${id}/timeline`),
  deleteVideo: (id: string) => req<void>(`/api/videos/${id}`, { method: "DELETE" }),
  patchVideo: (id: string, body: { title?: string; content_type?: ContentType }) =>
    req<void>(`/api/videos/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  process: (id: string, force = false) => req<{ job: unknown; deduplicated: boolean }>(`/api/videos/${id}/process`, json({ force })),
  importUrl: (body: { url: string; title?: string; content_type: ContentType }) => req<{ video: Video }>("/api/videos/import", json(body)),
  upload: (file: File, fields: Record<string, string>, onProgress: (f: number) => void) =>
    new Promise<{ video: Video; deduplicated: boolean }>((resolve, reject) => {
      // XHR (not fetch) so the UI can show upload progress.
      const fd = new FormData();
      Object.entries(fields).forEach(([k, v]) => fd.append(k, v));
      fd.append("file", file);
      const x = new XMLHttpRequest();
      x.open("POST", "/api/videos");
      x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      x.onload = () => {
        const body = JSON.parse(x.responseText || "{}");
        if (x.status >= 300) reject(new ApiError(x.status, body.error ?? x.statusText));
        else resolve(body);
      };
      x.onerror = () => reject(new ApiError(0, "network error"));
      x.send(fd);
    }),
  search: (p: SearchParams) => req<SearchResponse>("/api/search", json(p)),
  feedback: (search_id: string, rank: number, label: "relevant" | "irrelevant") =>
    req<unknown>("/api/feedback", json({ search_id, rank, label })),
  bookmarks: () => req<Bookmark[]>("/api/bookmarks"),
  addBookmark: (b: { video_id: string; start_ms: number; end_ms: number; title: string; note?: string; query?: string; search_id?: string; client_request_id: string }) =>
    req<{ bookmark: Bookmark; created: boolean }>("/api/bookmarks", json(b)),
  deleteBookmark: (id: string) => req<void>(`/api/bookmarks/${id}`, { method: "DELETE" }),
  conversations: () => req<{ id: string; title: string; updated_at: string }[]>("/api/conversations"),
  newConversation: () => req<{ id: string }>("/api/conversations", json({})),
  conversation: (id: string) => req<{ id: string; title: string; turns: ConversationTurn[] }>(`/api/conversations/${id}`),
  say: (id: string, text: string, client_turn_id: string, resume = false) =>
    req<TurnResponse>(`/api/conversations/${id}/messages`, json({ text, client_turn_id, resume })),
  evalQueries: () => req<EvalQuery[]>("/api/eval/queries"),
  addEvalQuery: (q: { query: string; query_type: string; notes: string; answers: { video_id: string; start_ms: number; end_ms: number }[] }) =>
    req<{ id: string }>("/api/eval/queries", json(q)),
  deleteEvalQuery: (id: string) => req<void>(`/api/eval/queries/${id}`, { method: "DELETE" }),
  evalReport: () => req<EvalReport>("/api/eval/report"),
};

// In the GitHub Pages build, reads go to recorded JSON; everything else is hidden by the UI.
export const api: typeof liveApi = DEMO
  ? { ...liveApi, videos: demoVideos, search: (p: SearchParams) => demoSearch(p.query, p.ranker === "auto" ? "E" : p.ranker) }
  : liveApi;

export interface ArmSummary {
  n_answerable: number;
  n_no_answer: number;
  [k: string]: unknown;
}

export interface EvalReport {
  available?: false;
  created: string;
  split: string;
  n_queries: number;
  query_types: Record<string, number>;
  model_id: string | null;
  primary_arm: string;
  corpus: { videos: number; hours: number; segments: number; indexing_wall_seconds_per_video_hour: Record<string, number> };
  arms: Record<string, { label: string; overall: ArmSummary; by_query_type: Record<string, ArmSummary>; by_content_type: Record<string, ArmSummary> }>;
  failures: { id: string; query: string; type: string; category: string }[];
  latency?: Record<string, { n: number; p50_ms: number; p95_ms: number; embed_p50_ms: number; retrieval_p50_ms: number }>;
}
