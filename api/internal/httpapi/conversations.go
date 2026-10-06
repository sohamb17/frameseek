package httpapi

import (
	"errors"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"

	"github.com/sohamb17/frameseek/api/internal/mlclient"
)

func (s *Server) listConversations(w http.ResponseWriter, r *http.Request) {
	rows, err := s.store.ListConversations(r.Context(), owner(r))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) createConversation(w http.ResponseWriter, r *http.Request) {
	var body struct {
		CollectionID string `json:"collection_id"`
	}
	if r.ContentLength > 0 {
		if err := decode(r, &body); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid body")
			return
		}
	}
	coll, err := s.resolveCollection(r, body.CollectionID)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	row, err := s.store.CreateConversation(r.Context(), owner(r), coll)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, row)
}

func (s *Server) decorateTurnResponse(resp any) {
	m, ok := resp.(map[string]any)
	if !ok {
		return
	}
	if results, ok := m["results"].([]any); ok {
		s.decorateResults(results)
	}
}

func (s *Server) getConversation(w http.ResponseWriter, r *http.Request) {
	conv, err := s.store.GetConversation(r.Context(), owner(r), chi.URLParam(r, "id"))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if turns, ok := conv["turns"].([]map[string]any); ok {
		for _, t := range turns {
			s.decorateTurnResponse(t["response"])
		}
	}
	writeJSON(w, http.StatusOK, conv)
}

// postMessage runs one conversational turn. The conversation's owner and collection come from
// the stored conversation row, so nothing the user (or the language model) types can widen scope.
func (s *Server) postMessage(w http.ResponseWriter, r *http.Request) {
	var body struct {
		ClientTurnID string `json:"client_turn_id"`
		Text         string `json:"text"`
		Resume       bool   `json:"resume"`
	}
	if err := decode(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid body")
		return
	}
	body.Text = strings.TrimSpace(body.Text)
	if body.ClientTurnID == "" || len(body.ClientTurnID) > 100 || body.Text == "" || len(body.Text) > 1000 {
		writeErr(w, http.StatusBadRequest, "need client_turn_id and text (max 1000 chars)")
		return
	}
	convID := chi.URLParam(r, "id")
	conv, err := s.store.GetConversation(r.Context(), owner(r), convID)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	seq, existing, err := s.store.BeginTurn(r.Context(), convID, body.ClientTurnID, body.Text)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if existing != nil {
		// Duplicate submission (retry, double click): return what the first one produced.
		if existing["status"] == "pending" {
			writeErr(w, http.StatusConflict, "this turn is still being processed")
			return
		}
		resp, _ := existing["response"].(map[string]any)
		if resp == nil {
			resp = map[string]any{}
		}
		s.decorateTurnResponse(resp)
		resp["duplicate"] = true
		writeJSON(w, http.StatusOK, resp)
		return
	}
	req := map[string]any{
		"thread_id": convID, "owner_id": owner(r), "collection_id": conv["collection_id"],
		"seq": seq, "text": body.Text, "resume": body.Resume,
	}
	var out map[string]any
	if err := s.ml.Post(r.Context(), "/conversation/turn", s.cfg.ConvTimeout, req, &out); err != nil {
		s.store.FinishTurn(r.Context(), convID, body.ClientTurnID, "failed", map[string]any{"error": err.Error()}) //nolint:errcheck
		if errors.Is(err, mlclient.ErrTimeout) {
			writeErr(w, http.StatusGatewayTimeout, "the conversation service did not answer in time")
			return
		}
		s.fail(w, r, err)
		return
	}
	if out["status"] == "stale" {
		s.store.FinishTurn(r.Context(), convID, body.ClientTurnID, "stale", out) //nolint:errcheck
		writeErr(w, http.StatusConflict, "a newer message in this conversation was already answered")
		return
	}
	if err := s.store.FinishTurn(r.Context(), convID, body.ClientTurnID, "done", out); err != nil {
		s.fail(w, r, err)
		return
	}
	s.decorateTurnResponse(out)
	out["seq"] = seq
	writeJSON(w, http.StatusOK, out)
}
