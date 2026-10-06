package httpapi

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/sohamb17/frameseek/api/internal/store"
)

type searchRequest struct {
	Query           string   `json:"query"`
	CollectionID    string   `json:"collection_id,omitempty"`
	VideoIDs        []string `json:"video_ids,omitempty"`
	ExcludeVideoIDs []string `json:"exclude_video_ids,omitempty"`
	ContentTypes    []string `json:"content_types,omitempty"`
	K               int      `json:"k,omitempty"`
	Ranker          string   `json:"ranker,omitempty"`
}

var rankers = map[string]bool{"": true, "auto": true, "learned": true, "fusion": true,
	"A": true, "B": true, "C": true, "D": true, "E": true, "F": true}

func (q *searchRequest) validate() string {
	q.Query = strings.TrimSpace(q.Query)
	if q.Query == "" || len(q.Query) > 500 {
		return "query must be 1-500 characters"
	}
	if q.K == 0 {
		q.K = 10
	}
	if q.K < 1 || q.K > 50 {
		return "k must be between 1 and 50"
	}
	if !rankers[q.Ranker] {
		return "unknown ranker"
	}
	for _, id := range append(append([]string{}, q.VideoIDs...), q.ExcludeVideoIDs...) {
		if _, err := uuid.Parse(id); err != nil {
			return "video ids must be UUIDs"
		}
	}
	for _, ct := range q.ContentTypes {
		if !contentTypes[ct] {
			return "unknown content type " + ct
		}
	}
	return ""
}

// decorateResults adds server-constructed, signed media links to result cards.
func (s *Server) decorateResults(results []any) {
	for _, item := range results {
		res, ok := item.(map[string]any)
		if !ok {
			continue
		}
		if vid, ok := res["video_id"].(string); ok {
			res["playback_url"] = s.signer.Sign("/media/videos/" + vid + "/playback")
		}
		if fid, ok := res["thumb_frame_id"].(float64); ok {
			res["thumb_url"] = s.signer.Sign(fmt.Sprintf("/media/frames/%d/thumb", int64(fid)))
		}
		if ev, ok := res["evidence"].(map[string]any); ok {
			if vis, ok := ev["visual"].(map[string]any); ok {
				if fid, ok := vis["frame_id"].(float64); ok {
					vis["thumb_url"] = s.signer.Sign(fmt.Sprintf("/media/frames/%d/thumb", int64(fid)))
				}
			}
		}
	}
}

func (s *Server) search(w http.ResponseWriter, r *http.Request) {
	var q searchRequest
	if err := decode(r, &q); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid body: "+err.Error())
		return
	}
	if msg := q.validate(); msg != "" {
		writeErr(w, http.StatusBadRequest, msg)
		return
	}
	if q.CollectionID != "" {
		if _, err := s.resolveCollection(r, q.CollectionID); err != nil {
			s.fail(w, r, err)
			return
		}
	}
	body := map[string]any{
		"query": q.Query, "owner_id": owner(r), "k": q.K, "ranker": q.Ranker,
		"collection_id": nilIfEmpty(q.CollectionID), "video_ids": q.VideoIDs,
		"exclude_video_ids": q.ExcludeVideoIDs, "content_types": q.ContentTypes,
	}
	if body["ranker"] == "" {
		body["ranker"] = "auto"
	}
	var out map[string]any
	if err := s.ml.Post(r.Context(), "/search", s.cfg.SearchTimeout, body, &out); err != nil {
		s.fail(w, r, err)
		return
	}
	if results, ok := out["results"].([]any); ok {
		s.decorateResults(results)
	}
	writeJSON(w, http.StatusOK, out)
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func (s *Server) feedback(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SearchID string `json:"search_id"`
		Rank     int    `json:"rank"`
		Label    string `json:"label"`
	}
	if err := decode(r, &body); err != nil || (body.Label != "relevant" && body.Label != "irrelevant") || body.Rank < 1 {
		writeErr(w, http.StatusBadRequest, "need search_id, rank >= 1 and label relevant|irrelevant")
		return
	}
	row, err := s.store.RecordFeedback(r.Context(), owner(r), body.SearchID, body.Rank, body.Label)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, row)
}

func (s *Server) feedbackStats(w http.ResponseWriter, r *http.Request) {
	row, err := s.store.FeedbackStats(r.Context(), owner(r))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, row)
}

func (s *Server) decorateBookmark(b store.Row) store.Row {
	if vid, ok := b["video_id"].(string); ok {
		b["playback_url"] = s.signer.Sign("/media/videos/" + vid + "/playback")
	}
	if fid, ok := b["thumb_frame_id"].(int64); ok {
		b["thumb_url"] = s.signer.Sign(fmt.Sprintf("/media/frames/%d/thumb", fid))
	}
	return b
}

func (s *Server) listBookmarks(w http.ResponseWriter, r *http.Request) {
	rows, err := s.store.ListBookmarks(r.Context(), owner(r))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	for _, b := range rows {
		s.decorateBookmark(b)
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) createBookmark(w http.ResponseWriter, r *http.Request) {
	var b store.NewBookmark
	if err := decode(r, &b); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid body: "+err.Error())
		return
	}
	if _, err := uuid.Parse(b.VideoID); err != nil || b.ClientRequestID == "" || len(b.ClientRequestID) > 100 {
		writeErr(w, http.StatusBadRequest, "need video_id and client_request_id")
		return
	}
	if len(b.Title) > 200 || len(b.Note) > 2000 {
		writeErr(w, http.StatusBadRequest, "title/note too long")
		return
	}
	row, created, err := s.store.CreateBookmark(r.Context(), owner(r), b)
	if err != nil {
		if strings.Contains(err.Error(), "invalid interval") {
			writeErr(w, http.StatusBadRequest, "end must be after start")
			return
		}
		s.fail(w, r, err)
		return
	}
	status := http.StatusCreated
	if !created {
		status = http.StatusOK
	}
	writeJSON(w, status, map[string]any{"bookmark": s.decorateBookmark(row), "created": created})
}

func (s *Server) deleteBookmark(w http.ResponseWriter, r *http.Request) {
	ok, err := s.store.DeleteBookmark(r.Context(), owner(r), chi.URLParam(r, "id"))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if !ok {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
