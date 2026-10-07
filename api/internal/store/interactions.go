package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// ----------------------------------------------------------------- feedback

// RecordFeedback stores a judgment for the result the user actually saw: the (video, version,
// interval) is copied from the stored search snapshot, not from the request body.
func (s *Store) RecordFeedback(ctx context.Context, owner, searchID string, rank int, label string) (Row, error) {
	if _, err := uuid.Parse(searchID); err != nil {
		return nil, ErrNotFound
	}
	var raw []byte
	err := s.Pool.QueryRow(ctx, `SELECT results FROM searches WHERE id = $1 AND owner_id = $2`, searchID, owner).Scan(&raw)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	var results []struct {
		Rank         int    `json:"rank"`
		VideoID      string `json:"video_id"`
		IndexVersion int    `json:"index_version"`
		SegmentID    int64  `json:"segment_id"`
		StartMs      int64  `json:"start_ms"`
		EndMs        int64  `json:"end_ms"`
	}
	if err := json.Unmarshal(raw, &results); err != nil {
		return nil, err
	}
	for _, r := range results {
		if r.Rank != rank {
			continue
		}
		return oneMap(s.Pool.Query(ctx, `
			INSERT INTO feedback (owner_id, search_id, rank, video_id, index_version, segment_id, start_ms, end_ms, label)
			VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
			ON CONFLICT (owner_id, search_id, rank) DO UPDATE SET label = EXCLUDED.label, created_at = now()
			RETURNING id::text AS id, label, rank`,
			owner, searchID, rank, r.VideoID, r.IndexVersion, r.SegmentID, r.StartMs, r.EndMs, label))
	}
	return nil, fmt.Errorf("rank %d not in search %s: %w", rank, searchID, ErrNotFound)
}

func (s *Store) FeedbackStats(ctx context.Context, owner string) (Row, error) {
	return oneMap(s.Pool.Query(ctx, `SELECT count(*) FILTER (WHERE label = 'relevant') AS relevant,
		count(*) FILTER (WHERE label = 'irrelevant') AS irrelevant FROM feedback WHERE owner_id = $1`, owner))
}

// ----------------------------------------------------------------- bookmarks

type NewBookmark struct {
	VideoID         string `json:"video_id"`
	StartMs         int64  `json:"start_ms"`
	EndMs           int64  `json:"end_ms"`
	Title           string `json:"title"`
	Note            string `json:"note"`
	Query           string `json:"query"`
	SearchID        string `json:"search_id"`
	ClientRequestID string `json:"client_request_id"`
}

// CreateBookmark is idempotent per client_request_id, so a double-click or retried request
// never creates two bookmarks. It pins the video's current index version.
func (s *Store) CreateBookmark(ctx context.Context, owner string, b NewBookmark) (Row, bool, error) {
	if b.EndMs <= b.StartMs || b.StartMs < 0 {
		return nil, false, fmt.Errorf("invalid interval")
	}
	var durationMs *int64
	var version *int
	err := s.Pool.QueryRow(ctx, `SELECT duration_ms, active_index_version FROM videos WHERE id = $1 AND owner_id = $2`,
		b.VideoID, owner).Scan(&durationMs, &version)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, false, ErrNotFound
	}
	if err != nil {
		return nil, false, err
	}
	if durationMs != nil && b.EndMs > *durationMs {
		b.EndMs = *durationMs
	}
	var searchID any
	if _, err := uuid.Parse(b.SearchID); err == nil {
		searchID = b.SearchID
	}
	row, err := oneMap(s.Pool.Query(ctx, `
		INSERT INTO bookmarks (owner_id, video_id, index_version, start_ms, end_ms, title, note, query, search_id, client_request_id)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
		ON CONFLICT (owner_id, client_request_id) DO NOTHING
		RETURNING id::text AS id`, owner, b.VideoID, version, b.StartMs, b.EndMs, b.Title, b.Note,
		nullable(b.Query), searchID, b.ClientRequestID))
	created := err == nil
	if err != nil && !errors.Is(err, ErrNotFound) {
		return nil, false, err
	}
	var id string
	if created {
		id = row["id"].(string)
	} else if err := s.Pool.QueryRow(ctx, `SELECT id::text FROM bookmarks WHERE owner_id = $1 AND client_request_id = $2`,
		owner, b.ClientRequestID).Scan(&id); err != nil {
		return nil, false, err
	}
	list, err := s.listBookmarks(ctx, owner, id)
	if err != nil || len(list) == 0 {
		return nil, false, err
	}
	return list[0], created, nil
}

func (s *Store) listBookmarks(ctx context.Context, owner, id string) ([]Row, error) {
	q := `SELECT b.id::text AS id, b.video_id::text AS video_id, v.title AS video_title, v.content_type,
	             b.index_version, b.start_ms, b.end_ms, b.title, b.note, b.query, b.created_at,
	             (SELECT f.id FROM frames f WHERE f.video_id = b.video_id AND f.index_version = b.index_version
	                AND f.ts_ms >= b.start_ms ORDER BY f.ts_ms LIMIT 1) AS thumb_frame_id
	      FROM bookmarks b JOIN videos v ON v.id = b.video_id WHERE b.owner_id = $1`
	args := []any{owner}
	if id != "" {
		q += ` AND b.id = $2`
		args = append(args, id)
	}
	return rowsToMaps(s.Pool.Query(ctx, q+` ORDER BY b.created_at DESC`, args...))
}

func (s *Store) ListBookmarks(ctx context.Context, owner string) ([]Row, error) {
	return s.listBookmarks(ctx, owner, "")
}

func (s *Store) DeleteBookmark(ctx context.Context, owner, id string) (bool, error) {
	if _, err := uuid.Parse(id); err != nil {
		return false, nil
	}
	tag, err := s.Pool.Exec(ctx, `DELETE FROM bookmarks WHERE id = $1 AND owner_id = $2`, id, owner)
	return tag.RowsAffected() == 1, err
}

// ----------------------------------------------------------------- conversations

func (s *Store) CreateConversation(ctx context.Context, owner, collection string) (Row, error) {
	return oneMap(s.Pool.Query(ctx, `INSERT INTO conversations (owner_id, collection_id) VALUES ($1, $2)
		RETURNING id::text AS id, collection_id::text AS collection_id, title, created_at`, owner, nullable(collection)))
}

func (s *Store) GetConversation(ctx context.Context, owner, id string) (Row, error) {
	if _, err := uuid.Parse(id); err != nil {
		return nil, ErrNotFound
	}
	conv, err := oneMap(s.Pool.Query(ctx, `SELECT id::text AS id, collection_id::text AS collection_id, title,
		last_seq, created_at, updated_at FROM conversations WHERE id = $1 AND owner_id = $2`, id, owner))
	if err != nil {
		return nil, err
	}
	turns, err := rowsToMaps(s.Pool.Query(ctx, `SELECT client_turn_id, seq, user_text, status, response, created_at
		FROM conversation_turns WHERE conversation_id = $1 ORDER BY seq`, id))
	if err != nil {
		return nil, err
	}
	conv["turns"] = turns
	return conv, nil
}

func (s *Store) ListConversations(ctx context.Context, owner string) ([]Row, error) {
	return rowsToMaps(s.Pool.Query(ctx, `SELECT id::text AS id, title, last_seq, created_at, updated_at
		FROM conversations WHERE owner_id = $1 ORDER BY updated_at DESC LIMIT 50`, owner))
}

// BeginTurn reserves the next sequence number for a new client turn. A repeated client_turn_id
// returns the stored turn instead (duplicate submission is a no-op).
func (s *Store) BeginTurn(ctx context.Context, convID, clientTurnID, text string) (seq int, existing Row, err error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return 0, nil, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	existing, err = oneMap(tx.Query(ctx, `SELECT seq, status, response FROM conversation_turns
		WHERE conversation_id = $1 AND client_turn_id = $2`, convID, clientTurnID))
	if err == nil {
		return 0, existing, nil
	}
	if !errors.Is(err, ErrNotFound) {
		return 0, nil, err
	}
	if err := tx.QueryRow(ctx, `UPDATE conversations SET last_seq = last_seq + 1, updated_at = now(),
		title = CASE WHEN last_seq = 0 THEN left($2, 60) ELSE title END
		WHERE id = $1 RETURNING last_seq`, convID, text).Scan(&seq); err != nil {
		return 0, nil, err
	}
	if _, err := tx.Exec(ctx, `INSERT INTO conversation_turns (conversation_id, client_turn_id, seq, user_text)
		VALUES ($1,$2,$3,$4)`, convID, clientTurnID, seq, text); err != nil {
		return 0, nil, err
	}
	return seq, nil, tx.Commit(ctx)
}

func (s *Store) FinishTurn(ctx context.Context, convID, clientTurnID, status string, response any) error {
	raw, err := json.Marshal(response)
	if err != nil {
		return err
	}
	_, err = s.Pool.Exec(ctx, `UPDATE conversation_turns SET status = $3, response = $4
		WHERE conversation_id = $1 AND client_turn_id = $2`, convID, clientTurnID, status, raw)
	return err
}

// ----------------------------------------------------------------- evaluation labels

type EvalAnswer struct {
	VideoID string `json:"video_id"`
	StartMs int64  `json:"start_ms"`
	EndMs   int64  `json:"end_ms"`
}

type NewEvalQuery struct {
	Query     string       `json:"query"`
	QueryType string       `json:"query_type"`
	Notes     string       `json:"notes"`
	Answers   []EvalAnswer `json:"answers"`
}

// ErrInvalid marks a label that fails validation (reported to the client as 400).
var ErrInvalid = errors.New("invalid label")

func validateEvalQuery(q NewEvalQuery) error {
	if q.QueryType != "no_answer" && len(q.Answers) == 0 {
		return fmt.Errorf("%w: a %s query needs at least one answer interval", ErrInvalid, q.QueryType)
	}
	if q.QueryType == "no_answer" && len(q.Answers) > 0 {
		return fmt.Errorf("%w: a no_answer query cannot have answer intervals", ErrInvalid)
	}
	for _, a := range q.Answers {
		if a.EndMs <= a.StartMs || a.StartMs < 0 {
			return fmt.Errorf("%w: answer interval must have 0 <= start < end", ErrInvalid)
		}
	}
	return nil
}

// insertAnswers writes answer intervals, checking that every video belongs to the owner.
func insertAnswers(ctx context.Context, tx pgx.Tx, owner, queryID string, answers []EvalAnswer) error {
	for _, a := range answers {
		tag, err := tx.Exec(ctx, `INSERT INTO eval_answers (query_id, video_id, start_ms, end_ms)
			SELECT $1, id, $3, $4 FROM videos WHERE id = $2 AND owner_id = $5`, queryID, a.VideoID, a.StartMs, a.EndMs, owner)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return ErrNotFound
		}
	}
	return nil
}

func (s *Store) CreateEvalQuery(ctx context.Context, owner string, q NewEvalQuery) (Row, error) {
	if err := validateEvalQuery(q); err != nil {
		return nil, err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var id string
	if err := tx.QueryRow(ctx, `INSERT INTO eval_queries (owner_id, query, query_type, notes) VALUES ($1,$2,$3,$4)
		RETURNING id::text`, owner, q.Query, q.QueryType, q.Notes).Scan(&id); err != nil {
		return nil, err
	}
	if err := insertAnswers(ctx, tx, owner, id, q.Answers); err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return Row{"id": id}, nil
}

// UpdateEvalQuery replaces a label's text, type, notes and answers.
//
// For assistant-written labels this is also the human review step: the first save stamps
// reviewed_at and records a verdict, "accepted" when nothing changed and "corrected" otherwise.
// Verdicts on the random spot-check sample are the measured agreement reported with results.
func (s *Store) UpdateEvalQuery(ctx context.Context, owner, id string, q NewEvalQuery) (Row, error) {
	if _, err := uuid.Parse(id); err != nil {
		return nil, ErrNotFound
	}
	if err := validateEvalQuery(q); err != nil {
		return nil, err
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var (
		author, oldQuery, oldType string
		reviewed                  bool
		verdict                   *string
	)
	err = tx.QueryRow(ctx, `SELECT author, query, query_type, reviewed_at IS NOT NULL, review_verdict
		FROM eval_queries WHERE id = $1 AND owner_id = $2 FOR UPDATE`, id, owner).
		Scan(&author, &oldQuery, &oldType, &reviewed, &verdict)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	var old []EvalAnswer
	rows, err := tx.Query(ctx, `SELECT video_id::text, start_ms, end_ms FROM eval_answers WHERE query_id = $1`, id)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var a EvalAnswer
		if err := rows.Scan(&a.VideoID, &a.StartMs, &a.EndMs); err != nil {
			rows.Close()
			return nil, err
		}
		old = append(old, a)
	}
	rows.Close()
	changed := strings.TrimSpace(oldQuery) != strings.TrimSpace(q.Query) || oldType != q.QueryType || !sameAnswers(old, q.Answers)

	newVerdict := verdict
	if author != "owner" {
		v := ""
		switch {
		case !reviewed && changed:
			v = "corrected"
		case !reviewed:
			v = "accepted"
		case changed && verdict != nil && *verdict == "accepted":
			v = "corrected"
		}
		if v != "" {
			newVerdict = &v
		}
	}
	if _, err := tx.Exec(ctx, `UPDATE eval_queries SET query = $2, query_type = $3, notes = $4, review_verdict = $5,
		reviewed_at = CASE WHEN author <> 'owner' THEN coalesce(reviewed_at, now()) ELSE reviewed_at END
		WHERE id = $1`, id, q.Query, q.QueryType, q.Notes, newVerdict); err != nil {
		return nil, err
	}
	if _, err := tx.Exec(ctx, `DELETE FROM eval_answers WHERE query_id = $1`, id); err != nil {
		return nil, err
	}
	if err := insertAnswers(ctx, tx, owner, id, q.Answers); err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return Row{"id": id, "author": author, "review_verdict": newVerdict}, nil
}

// RejectEvalQuery marks an assistant-written label as wrong. The row is kept (and excluded from
// training and evaluation) so the rejection still counts in the reported agreement.
func (s *Store) RejectEvalQuery(ctx context.Context, owner, id string) error {
	if _, err := uuid.Parse(id); err != nil {
		return ErrNotFound
	}
	var author string
	err := s.Pool.QueryRow(ctx, `SELECT author FROM eval_queries WHERE id = $1 AND owner_id = $2`, id, owner).Scan(&author)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	if err != nil {
		return err
	}
	if author == "owner" {
		return fmt.Errorf("%w: only assistant-written labels can be rejected; delete your own labels instead", ErrInvalid)
	}
	_, err = s.Pool.Exec(ctx, `UPDATE eval_queries SET review_verdict = 'rejected', reviewed_at = coalesce(reviewed_at, now())
		WHERE id = $1`, id)
	return err
}

// sameAnswers compares answer sets ignoring order.
func sameAnswers(a, b []EvalAnswer) bool {
	if len(a) != len(b) {
		return false
	}
	key := func(x EvalAnswer) string {
		return fmt.Sprintf("%s/%d/%d", strings.ToLower(x.VideoID), x.StartMs, x.EndMs)
	}
	count := map[string]int{}
	for _, x := range a {
		count[key(x)]++
	}
	for _, x := range b {
		count[key(x)]--
		if count[key(x)] < 0 {
			return false
		}
	}
	return true
}

func (s *Store) ListEvalQueries(ctx context.Context, owner string) ([]Row, error) {
	return rowsToMaps(s.Pool.Query(ctx, `
		SELECT q.id::text AS id, q.query, q.query_type, q.notes, q.author, q.reviewed_at, q.spot_check, q.review_verdict, q.created_at,
		       coalesce(json_agg(json_build_object('video_id', a.video_id, 'video_title', v.title,
		                 'start_ms', a.start_ms, 'end_ms', a.end_ms)) FILTER (WHERE a.id IS NOT NULL), '[]') AS answers
		FROM eval_queries q LEFT JOIN eval_answers a ON a.query_id = q.id LEFT JOIN videos v ON v.id = a.video_id
		WHERE q.owner_id = $1 GROUP BY q.id ORDER BY q.created_at DESC`, owner))
}

func (s *Store) DeleteEvalQuery(ctx context.Context, owner, id string) (bool, error) {
	if _, err := uuid.Parse(id); err != nil {
		return false, nil
	}
	tag, err := s.Pool.Exec(ctx, `DELETE FROM eval_queries WHERE id = $1 AND owner_id = $2`, id, owner)
	return tag.RowsAffected() == 1, err
}
