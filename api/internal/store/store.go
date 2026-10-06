// Package store holds every SQL statement the API runs. Every query is scoped by owner_id,
// which comes from the authenticated request, never from the request body.
package store

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

var ErrNotFound = errors.New("not found")

type Store struct {
	Pool      *pgxpool.Pool
	ConfigDir string
}

type Row = map[string]any

func rowsToMaps(rows pgx.Rows, err error) ([]Row, error) {
	if err != nil {
		return nil, err
	}
	out, err := pgx.CollectRows(rows, pgx.RowToMap)
	if out == nil {
		out = []Row{}
	}
	return out, err
}

func oneMap(rows pgx.Rows, err error) (Row, error) {
	list, err := rowsToMaps(rows, err)
	if err != nil {
		return nil, err
	}
	if len(list) == 0 {
		return nil, ErrNotFound
	}
	return list[0], nil
}

// ----------------------------------------------------------------- collections

func (s *Store) DefaultCollection(ctx context.Context, owner string) (string, error) {
	var id string
	err := s.Pool.QueryRow(ctx, `
		INSERT INTO collections (owner_id, name) VALUES ($1, 'My library')
		ON CONFLICT (owner_id, name) DO UPDATE SET name = EXCLUDED.name
		RETURNING id::text`, owner).Scan(&id)
	return id, err
}

func (s *Store) ListCollections(ctx context.Context, owner string) ([]Row, error) {
	return rowsToMaps(s.Pool.Query(ctx, `
		SELECT c.id::text AS id, c.name, c.created_at,
		       (SELECT count(*) FROM videos v WHERE v.collection_id = c.id) AS video_count
		FROM collections c WHERE c.owner_id = $1 ORDER BY c.created_at`, owner))
}

func (s *Store) CreateCollection(ctx context.Context, owner, name string) (Row, error) {
	return oneMap(s.Pool.Query(ctx, `
		INSERT INTO collections (owner_id, name) VALUES ($1, $2)
		ON CONFLICT (owner_id, name) DO UPDATE SET name = EXCLUDED.name
		RETURNING id::text AS id, name, created_at`, owner, name))
}

func (s *Store) CollectionOwned(ctx context.Context, owner, id string) (bool, error) {
	if _, err := uuid.Parse(id); err != nil {
		return false, nil
	}
	var ok bool
	err := s.Pool.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM collections WHERE id = $1 AND owner_id = $2)`, id, owner).Scan(&ok)
	return ok, err
}

// ----------------------------------------------------------------- videos

const videoCols = `
	v.id::text AS id, v.collection_id::text AS collection_id, v.title, v.content_type, v.source_kind,
	v.source_url, v.license, v.attribution, v.original_filename, v.size_bytes, v.content_hash, v.status,
	v.error, v.duration_ms, v.width, v.height, v.has_audio, v.active_index_version, v.created_at,
	v.updated_at, v.poster_key, v.playback_key, v.probe,
	j.id::text AS job_id, j.status AS job_status, j.stage AS job_stage, j.stage_progress AS job_progress,
	j.attempt AS job_attempt, j.error AS job_error, j.index_version AS job_index_version`

const latestJobJoin = `
	LEFT JOIN LATERAL (SELECT * FROM jobs WHERE jobs.video_id = v.id ORDER BY created_at DESC LIMIT 1) j ON true`

func (s *Store) ListVideos(ctx context.Context, owner, collection string) ([]Row, error) {
	q := `SELECT ` + videoCols + ` FROM videos v ` + latestJobJoin + ` WHERE v.owner_id = $1`
	args := []any{owner}
	if collection != "" {
		q += ` AND v.collection_id = $2`
		args = append(args, collection)
	}
	q += ` ORDER BY v.created_at DESC`
	return rowsToMaps(s.Pool.Query(ctx, q, args...))
}

func (s *Store) GetVideo(ctx context.Context, owner, id string) (Row, error) {
	if _, err := uuid.Parse(id); err != nil {
		return nil, ErrNotFound
	}
	return oneMap(s.Pool.Query(ctx, `SELECT `+videoCols+` FROM videos v `+latestJobJoin+
		` WHERE v.owner_id = $1 AND v.id = $2`, owner, id))
}

func (s *Store) VideoDetail(ctx context.Context, owner, id string) (Row, error) {
	v, err := s.GetVideo(ctx, owner, id)
	if err != nil {
		return nil, err
	}
	versions, err := rowsToMaps(s.Pool.Query(ctx, `
		SELECT version, status, config_hash, created_at, published_at FROM index_versions
		WHERE video_id = $1 ORDER BY version DESC`, id))
	if err != nil {
		return nil, err
	}
	jobs, err := rowsToMaps(s.Pool.Query(ctx, `
		SELECT id::text AS id, status, stage, stage_progress, attempt, index_version, error, created_at, finished_at
		FROM jobs WHERE video_id = $1 ORDER BY created_at DESC LIMIT 20`, id))
	if err != nil {
		return nil, err
	}
	manifests, err := rowsToMaps(s.Pool.Query(ctx, `
		SELECT index_version, stage, attempt, input_hash, config_hash, code_version, versions, outputs, metrics,
		       started_at, finished_at
		FROM stage_manifests WHERE video_id = $1 ORDER BY index_version DESC, started_at`, id))
	if err != nil {
		return nil, err
	}
	v["versions"], v["jobs"], v["manifests"] = versions, jobs, manifests
	return v, nil
}

type NewVideo struct {
	ID, Owner, Collection, Title, ContentType, SourceKind string
	SourceURL, License, Attribution, Filename           string
	OriginalKey, ContentHash, Status                     string
	Size                                                 int64
}

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func (s *Store) CreateVideo(ctx context.Context, v NewVideo) error {
	_, err := s.Pool.Exec(ctx, `
		INSERT INTO videos (id, owner_id, collection_id, title, content_type, source_kind, source_url, license,
		                    attribution, original_filename, original_key, content_hash, size_bytes, status)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)`,
		v.ID, v.Owner, v.Collection, v.Title, v.ContentType, v.SourceKind, nullable(v.SourceURL),
		nullable(v.License), nullable(v.Attribution), nullable(v.Filename), nullable(v.OriginalKey),
		nullable(v.ContentHash), v.Size, v.Status)
	return err
}

// FindByHash returns the id of a video with identical bytes in the collection, if any.
func (s *Store) FindByHash(ctx context.Context, collection, hash string) (string, error) {
	var id string
	err := s.Pool.QueryRow(ctx, `SELECT id::text FROM videos WHERE collection_id = $1 AND content_hash = $2`,
		collection, hash).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", nil
	}
	return id, err
}

// CompleteImport attaches the downloaded original to a URL-imported video.
func (s *Store) CompleteImport(ctx context.Context, id, key, hash string, size int64) error {
	_, err := s.Pool.Exec(ctx, `UPDATE videos SET original_key = $2, content_hash = $3, size_bytes = $4,
		status = 'registered', error = NULL, updated_at = now() WHERE id = $1`, id, key, hash, size)
	return err
}

func (s *Store) FailVideo(ctx context.Context, id, msg string) error {
	_, err := s.Pool.Exec(ctx, `UPDATE videos SET status = 'failed', error = $2, updated_at = now() WHERE id = $1`, id, msg)
	return err
}

// FailInterruptedImports runs at startup: an import that was in flight when the API stopped cannot resume.
func (s *Store) FailInterruptedImports(ctx context.Context) (int64, error) {
	tag, err := s.Pool.Exec(ctx, `UPDATE videos SET status = 'failed', error = 'import interrupted by a restart; import again',
		updated_at = now() WHERE status = 'importing'`)
	return tag.RowsAffected(), err
}

func (s *Store) DeleteVideo(ctx context.Context, owner, id string) (bool, error) {
	if _, err := uuid.Parse(id); err != nil {
		return false, nil
	}
	tag, err := s.Pool.Exec(ctx, `DELETE FROM videos WHERE id = $1 AND owner_id = $2`, id, owner)
	return tag.RowsAffected() == 1, err
}

func (s *Store) UpdateVideoMeta(ctx context.Context, owner, id, title, contentType string) (bool, error) {
	tag, err := s.Pool.Exec(ctx, `UPDATE videos SET title = coalesce(nullif($3, ''), title),
		content_type = coalesce(nullif($4, ''), content_type), updated_at = now()
		WHERE id = $1 AND owner_id = $2`, id, owner, title, contentType)
	return tag.RowsAffected() == 1, err
}

// ----------------------------------------------------------------- jobs

// IndexConfigHash is sha256 over the raw bytes of configs/index.yaml (Python computes it identically).
func (s *Store) IndexConfigHash() (string, []byte, error) {
	raw, err := os.ReadFile(filepath.Join(s.ConfigDir, "index.yaml"))
	if err != nil {
		return "", nil, fmt.Errorf("read index config: %w", err)
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), raw, nil
}

// CreateProcessJob is idempotent: the logical key is content hash + index config hash, so a
// duplicate submission returns the existing queued/running/succeeded job instead of a new one.
// force=true creates a new index version even with an unchanged config (used to reindex).
func (s *Store) CreateProcessJob(ctx context.Context, owner, videoID string, force bool) (Row, bool, error) {
	cfgHash, raw, err := s.IndexConfigHash()
	if err != nil {
		return nil, false, err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, false, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck

	var contentHash *string
	var status string
	err = tx.QueryRow(ctx, `SELECT content_hash, status FROM videos WHERE id = $1 AND owner_id = $2 FOR UPDATE`,
		videoID, owner).Scan(&contentHash, &status)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, ErrNotFound
	}
	if err != nil {
		return nil, false, err
	}
	if contentHash == nil {
		return nil, false, fmt.Errorf("video has no media yet (status %s)", status)
	}
	sum := sha256.Sum256([]byte(*contentHash + ":" + cfgHash))
	key := hex.EncodeToString(sum[:])
	if force {
		key += ":force:" + uuid.NewString()
	}
	existing, err := oneMap(tx.Query(ctx, `SELECT id::text AS id FROM jobs WHERE idempotency_key = $1
		AND status IN ('queued','running','succeeded') AND video_id = $2`, key, videoID))
	if err == nil {
		job, err := s.getJob(ctx, tx, owner, existing["id"].(string))
		return job, true, err
	}
	if !errors.Is(err, ErrNotFound) {
		return nil, false, err
	}
	var version int
	if err := tx.QueryRow(ctx, `SELECT coalesce(max(version), 0) + 1 FROM index_versions WHERE video_id = $1`,
		videoID).Scan(&version); err != nil {
		return nil, false, err
	}
	if _, err := tx.Exec(ctx, `INSERT INTO index_versions (video_id, version, config_hash, config)
		VALUES ($1, $2, $3, jsonb_build_object('yaml', $4::text))`, videoID, version, cfgHash, string(raw)); err != nil {
		return nil, false, err
	}
	var jobID string
	err = tx.QueryRow(ctx, `INSERT INTO jobs (video_id, owner_id, idempotency_key, index_version)
		VALUES ($1, $2, $3, $4) RETURNING id::text`, videoID, owner, key, version).Scan(&jobID)
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && pgErr.Code == "23505" {
		// Lost a race with a concurrent identical request: return the winner's job.
		tx.Rollback(ctx) //nolint:errcheck
		row, err := oneMap(s.Pool.Query(ctx, `SELECT id::text AS id FROM jobs WHERE idempotency_key = $1
			AND status IN ('queued','running','succeeded')`, key))
		if err != nil {
			return nil, false, err
		}
		job, err := s.GetJob(ctx, owner, row["id"].(string))
		return job, true, err
	}
	if err != nil {
		return nil, false, err
	}
	if _, err := tx.Exec(ctx, `UPDATE videos SET status = CASE WHEN active_index_version IS NULL THEN 'queued'
		ELSE status END, updated_at = now() WHERE id = $1`, videoID); err != nil {
		return nil, false, err
	}
	job, err := s.getJob(ctx, tx, owner, jobID)
	if err != nil {
		return nil, false, err
	}
	return job, false, tx.Commit(ctx)
}

type querier interface {
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
}

func (s *Store) getJob(ctx context.Context, q querier, owner, id string) (Row, error) {
	if _, err := uuid.Parse(id); err != nil {
		return nil, ErrNotFound
	}
	return oneMap(q.Query(ctx, `
		SELECT id::text AS id, video_id::text AS video_id, status, stage, stage_progress, attempt, max_attempts,
		       index_version, error, lease_owner, lease_expires_at, heartbeat_at, created_at, started_at,
		       finished_at, updated_at
		FROM jobs WHERE id = $1 AND owner_id = $2`, id, owner))
}

func (s *Store) GetJob(ctx context.Context, owner, id string) (Row, error) {
	return s.getJob(ctx, s.Pool, owner, id)
}

// ----------------------------------------------------------------- media lookups (for signed URLs)

func (s *Store) PlaybackKey(ctx context.Context, videoID string) (string, error) {
	var key *string
	err := s.Pool.QueryRow(ctx, `SELECT coalesce(playback_key, original_key) FROM videos WHERE id = $1`, videoID).Scan(&key)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && key == nil) {
		return "", ErrNotFound
	}
	return deref(key), err
}

func (s *Store) PosterKey(ctx context.Context, videoID string) (string, error) {
	var key *string
	err := s.Pool.QueryRow(ctx, `SELECT poster_key FROM videos WHERE id = $1`, videoID).Scan(&key)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && key == nil) {
		return "", ErrNotFound
	}
	return deref(key), err
}

func (s *Store) FrameThumbKey(ctx context.Context, frameID int64) (string, error) {
	var key string
	err := s.Pool.QueryRow(ctx, `SELECT thumb_key FROM frames WHERE id = $1`, frameID).Scan(&key)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", ErrNotFound
	}
	return key, err
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// VideoTimeline returns the active version's segments (for the player's transcript/OCR panel).
func (s *Store) VideoTimeline(ctx context.Context, owner, id string) ([]Row, error) {
	return rowsToMaps(s.Pool.Query(ctx, `
		SELECT s.id, s.idx, s.start_ms, s.end_ms, s.transcript, s.ocr_text, s.speech_coverage, s.ocr_coverage,
		       s.thumb_frame_id
		FROM segments s JOIN videos v ON v.id = s.video_id AND v.active_index_version = s.index_version
		WHERE v.id = $1 AND v.owner_id = $2 ORDER BY s.idx`, id, owner))
}
