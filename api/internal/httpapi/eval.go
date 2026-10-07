package httpapi

import (
	"errors"
	"net/http"
	"os"
	"path/filepath"

	"github.com/go-chi/chi/v5"

	"github.com/sohamb17/frameseek/api/internal/store"
)

var queryTypes = map[string]bool{"speech": true, "ocr": true, "visual": true, "mixed": true, "no_answer": true}

func (s *Server) listEvalQueries(w http.ResponseWriter, r *http.Request) {
	rows, err := s.store.ListEvalQueries(r.Context(), owner(r))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) createEvalQuery(w http.ResponseWriter, r *http.Request) {
	var q store.NewEvalQuery
	if err := decode(r, &q); err != nil || q.Query == "" || len(q.Query) > 500 || !queryTypes[q.QueryType] {
		writeErr(w, http.StatusBadRequest, "need query and query_type (speech|ocr|visual|mixed|no_answer)")
		return
	}
	row, err := s.store.CreateEvalQuery(r.Context(), owner(r), q)
	if err != nil {
		s.labelErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, row)
}

func (s *Server) updateEvalQuery(w http.ResponseWriter, r *http.Request) {
	var q store.NewEvalQuery
	if err := decode(r, &q); err != nil || q.Query == "" || len(q.Query) > 500 || !queryTypes[q.QueryType] {
		writeErr(w, http.StatusBadRequest, "need query and query_type (speech|ocr|visual|mixed|no_answer)")
		return
	}
	row, err := s.store.UpdateEvalQuery(r.Context(), owner(r), chi.URLParam(r, "id"), q)
	if err != nil {
		s.labelErr(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, row)
}

func (s *Server) rejectEvalQuery(w http.ResponseWriter, r *http.Request) {
	if err := s.store.RejectEvalQuery(r.Context(), owner(r), chi.URLParam(r, "id")); err != nil {
		s.labelErr(w, r, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) labelErr(w http.ResponseWriter, r *http.Request, err error) {
	if errors.Is(err, store.ErrInvalid) {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	s.fail(w, r, err)
}

func (s *Server) deleteEvalQuery(w http.ResponseWriter, r *http.Request) {
	ok, err := s.store.DeleteEvalQuery(r.Context(), owner(r), chi.URLParam(r, "id"))
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

// evalReport serves the latest report written by `python -m frameseek.evaluate`.
func (s *Server) evalReport(w http.ResponseWriter, r *http.Request) {
	p := filepath.Join(s.cfg.DataDir, "eval", "reports", "latest.json")
	data, err := os.ReadFile(p)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"available": false})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(data) //nolint:errcheck
}
