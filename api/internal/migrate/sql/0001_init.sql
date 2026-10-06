-- FrameSeek schema v1.
-- Ownership: the Go API applies migrations at startup; Python reads/writes the same tables.
-- Design notes:
--   * Every searchable row carries (video_id, index_version). Search only reads rows whose
--     version equals videos.active_index_version, so a reindex can build a new version in
--     "staging" and publish it with a single pointer flip inside one transaction.
--   * jobs.attempt is an attempt *generation*. Every worker write is guarded by
--     "WHERE id = $job AND attempt = $my_attempt", so a stale worker cannot publish.
--   * Vector spaces are kept in separate tables with fixed dimensions
--     (MiniLM text = 384, OpenCLIP ViT-B/32 = 512). They are never compared to each other.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE collections (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id    TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, name)
);

CREATE TABLE videos (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id             TEXT        NOT NULL,
    collection_id        UUID        NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    title                TEXT        NOT NULL,
    content_type         TEXT        NOT NULL DEFAULT 'other'
                         CHECK (content_type IN ('talk', 'screencast', 'demo', 'other')),
    source_kind          TEXT        NOT NULL CHECK (source_kind IN ('upload', 'url')),
    source_url           TEXT,
    license              TEXT,
    attribution          TEXT,
    original_filename    TEXT,
    original_key         TEXT,
    size_bytes           BIGINT,
    content_hash         TEXT,
    -- registered -> importing -> queued -> processing -> ready | failed
    status               TEXT        NOT NULL DEFAULT 'registered',
    error                TEXT,
    probe                JSONB,
    duration_ms          BIGINT,
    width                INT,
    height               INT,
    has_audio            BOOLEAN,
    playback_key         TEXT,
    poster_key           TEXT,
    active_index_version INT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX videos_owner_idx ON videos (owner_id, collection_id);
-- Uploading the same bytes twice into a collection returns the existing video.
CREATE UNIQUE INDEX videos_dedupe_idx ON videos (collection_id, content_hash) WHERE content_hash IS NOT NULL;

CREATE TABLE index_versions (
    video_id     UUID        NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    version      INT         NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'building'
                 CHECK (status IN ('building', 'ready', 'failed', 'superseded')),
    config_hash  TEXT        NOT NULL,
    config       JSONB       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    PRIMARY KEY (video_id, version)
);

CREATE TABLE jobs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id         UUID        NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    owner_id         TEXT        NOT NULL,
    kind             TEXT        NOT NULL DEFAULT 'process',
    idempotency_key  TEXT        NOT NULL,
    index_version    INT         NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    stage            TEXT        NOT NULL DEFAULT 'queued',
    stage_progress   REAL        NOT NULL DEFAULT 0,
    attempt          INT         NOT NULL DEFAULT 0,
    max_attempts     INT         NOT NULL DEFAULT 3,
    not_before       TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner      TEXT,
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at     TIMESTAMPTZ,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- At most one live (queued/running/succeeded) job per logical key = content hash + config hash.
CREATE UNIQUE INDEX jobs_idempotency_idx ON jobs (idempotency_key)
    WHERE status IN ('queued', 'running', 'succeeded');
CREATE INDEX jobs_claim_idx ON jobs (status, not_before, created_at);
CREATE INDEX jobs_video_idx ON jobs (video_id, created_at DESC);

CREATE TABLE stage_manifests (
    video_id      UUID        NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    index_version INT         NOT NULL,
    stage         TEXT        NOT NULL,
    job_id        UUID        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    attempt       INT         NOT NULL,
    input_hash    TEXT        NOT NULL,
    config_hash   TEXT        NOT NULL,
    code_version  TEXT        NOT NULL,
    versions      JSONB       NOT NULL DEFAULT '{}',
    outputs       JSONB       NOT NULL DEFAULT '{}',
    metrics       JSONB       NOT NULL DEFAULT '{}',
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (video_id, index_version, stage)
);

-- Raw ASR observations (kept verbatim; segments are derived from them).
CREATE TABLE asr_segments (
    video_id       UUID   NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    index_version  INT    NOT NULL,
    idx            INT    NOT NULL,
    start_ms       BIGINT NOT NULL,
    end_ms         BIGINT NOT NULL,
    text           TEXT   NOT NULL,
    avg_logprob    REAL,
    no_speech_prob REAL,
    words          JSONB  NOT NULL DEFAULT '[]',
    PRIMARY KEY (video_id, index_version, idx)
);

CREATE TABLE frames (
    id            BIGSERIAL PRIMARY KEY,
    video_id      UUID    NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    index_version INT     NOT NULL,
    idx           INT     NOT NULL,
    ts_ms         BIGINT  NOT NULL,
    image_key     TEXT    NOT NULL,
    thumb_key     TEXT    NOT NULL,
    is_transition BOOLEAN NOT NULL DEFAULT false,
    diff_score    REAL,
    ocr_text      TEXT    NOT NULL DEFAULT '',
    ocr_conf      REAL,
    ocr_lines     JSONB   NOT NULL DEFAULT '[]',
    ocr_reused    BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (video_id, index_version, idx)
);
CREATE INDEX frames_time_idx ON frames (video_id, index_version, ts_ms);

CREATE TABLE frame_embeddings (
    frame_id  BIGINT PRIMARY KEY REFERENCES frames(id) ON DELETE CASCADE,
    model     TEXT        NOT NULL,
    embedding vector(512) NOT NULL
);

CREATE TABLE segments (
    id              BIGSERIAL PRIMARY KEY,
    video_id        UUID   NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    index_version   INT    NOT NULL,
    idx             INT    NOT NULL,
    start_ms        BIGINT NOT NULL,
    end_ms          BIGINT NOT NULL,
    transcript      TEXT   NOT NULL DEFAULT '',
    ocr_text        TEXT   NOT NULL DEFAULT '',
    speech_coverage REAL   NOT NULL DEFAULT 0,
    ocr_coverage    REAL   NOT NULL DEFAULT 0,
    ocr_stability   REAL   NOT NULL DEFAULT 0,
    n_frames        INT    NOT NULL DEFAULT 0,
    thumb_frame_id  BIGINT REFERENCES frames(id) ON DELETE SET NULL,
    tsv_transcript  tsvector GENERATED ALWAYS AS (to_tsvector('english', transcript)) STORED,
    tsv_ocr         tsvector GENERATED ALWAYS AS (to_tsvector('english', ocr_text)) STORED,
    UNIQUE (video_id, index_version, idx)
);
CREATE INDEX segments_version_idx ON segments (video_id, index_version);
CREATE INDEX segments_tsv_transcript_idx ON segments USING gin (tsv_transcript);
CREATE INDEX segments_tsv_ocr_idx ON segments USING gin (tsv_ocr);

CREATE TABLE segment_embeddings (
    segment_id BIGINT      NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    kind       TEXT        NOT NULL CHECK (kind IN ('transcript', 'ocr')),
    model      TEXT        NOT NULL,
    embedding  vector(384) NOT NULL,
    PRIMARY KEY (segment_id, kind)
);

CREATE TABLE model_versions (
    id             TEXT PRIMARY KEY,           -- sha256 prefix of the serialized pipeline
    name           TEXT        NOT NULL,
    feature_schema JSONB       NOT NULL,
    model_sha256   TEXT        NOT NULL,
    path           TEXT        NOT NULL,
    train_manifest JSONB       NOT NULL DEFAULT '{}',
    metrics        JSONB       NOT NULL DEFAULT '{}',
    is_active      BOOLEAN     NOT NULL DEFAULT false,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX model_versions_one_active ON model_versions (is_active) WHERE is_active;

-- Snapshot of every search, so feedback and conversation references point at what the
-- user actually saw (not a freshly re-ranked list).
CREATE TABLE searches (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      TEXT        NOT NULL,
    collection_id UUID,
    query         TEXT        NOT NULL,
    filters       JSONB       NOT NULL DEFAULT '{}',
    ranker        TEXT        NOT NULL,
    model_version TEXT,
    snapshot      JSONB       NOT NULL DEFAULT '{}',
    results       JSONB       NOT NULL DEFAULT '[]',
    timings       JSONB       NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX searches_owner_idx ON searches (owner_id, created_at DESC);

CREATE TABLE feedback (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      TEXT        NOT NULL,
    search_id     UUID        NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    rank          INT         NOT NULL,
    video_id      UUID        NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    index_version INT         NOT NULL,
    segment_id    BIGINT,
    start_ms      BIGINT      NOT NULL,
    end_ms        BIGINT      NOT NULL,
    label         TEXT        NOT NULL CHECK (label IN ('relevant', 'irrelevant')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, search_id, rank)
);

CREATE TABLE bookmarks (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id          TEXT        NOT NULL,
    video_id          UUID        NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    index_version     INT,
    start_ms          BIGINT      NOT NULL,
    end_ms            BIGINT      NOT NULL,
    title             TEXT        NOT NULL DEFAULT '',
    note              TEXT        NOT NULL DEFAULT '',
    query             TEXT,
    search_id         UUID        REFERENCES searches(id) ON DELETE SET NULL,
    client_request_id TEXT        NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (owner_id, client_request_id),
    CHECK (end_ms > start_ms)
);

-- Human-authored relevance labels (the evaluation / training set).
CREATE TABLE eval_queries (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id   TEXT        NOT NULL,
    query      TEXT        NOT NULL,
    query_type TEXT        NOT NULL CHECK (query_type IN ('speech', 'ocr', 'visual', 'mixed', 'no_answer')),
    author     TEXT        NOT NULL DEFAULT 'owner',
    notes      TEXT        NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE eval_answers (
    id         BIGSERIAL PRIMARY KEY,
    query_id   UUID   NOT NULL REFERENCES eval_queries(id) ON DELETE CASCADE,
    video_id   UUID   NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start_ms   BIGINT NOT NULL,
    end_ms     BIGINT NOT NULL,
    CHECK (end_ms > start_ms)
);

CREATE TABLE conversations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id      TEXT        NOT NULL,
    collection_id UUID        REFERENCES collections(id) ON DELETE CASCADE,
    title         TEXT        NOT NULL DEFAULT 'New conversation',
    last_seq      INT         NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE conversation_turns (
    conversation_id UUID        NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    client_turn_id  TEXT        NOT NULL,
    seq             INT         NOT NULL,
    user_text       TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed', 'stale')),
    response        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (conversation_id, client_turn_id),
    UNIQUE (conversation_id, seq)
);
