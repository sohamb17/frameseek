package httpapi

import (
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/sohamb17/frameseek/api/internal/store"
)

func (s *Server) verified(w http.ResponseWriter, r *http.Request) bool {
	q := r.URL.Query()
	if !s.signer.Verify(r.URL.Path, q.Get("exp"), q.Get("sig")) {
		writeErr(w, http.StatusForbidden, "invalid or expired media link")
		return false
	}
	return true
}

// serveKey streams a stored artifact with Range support (seeking in <video> uses Range requests).
func (s *Server) serveKey(w http.ResponseWriter, r *http.Request, key string, cache string) {
	root, _ := filepath.Abs(s.cfg.DataDir)
	p := filepath.Join(root, filepath.Clean("/"+key))
	if !strings.HasPrefix(p, root+string(filepath.Separator)) {
		writeErr(w, http.StatusBadRequest, "bad key")
		return
	}
	f, err := os.Open(p)
	if err != nil {
		writeErr(w, http.StatusNotFound, "media not found")
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		writeErr(w, http.StatusNotFound, "media not found")
		return
	}
	switch strings.ToLower(filepath.Ext(p)) {
	case ".mp4":
		w.Header().Set("Content-Type", "video/mp4")
	case ".mov":
		w.Header().Set("Content-Type", "video/quicktime")
	case ".webm":
		w.Header().Set("Content-Type", "video/webm")
	case ".mkv":
		w.Header().Set("Content-Type", "video/x-matroska")
	case ".jpg":
		w.Header().Set("Content-Type", "image/jpeg")
	}
	w.Header().Set("Cache-Control", cache)
	http.ServeContent(w, r, filepath.Base(p), st.ModTime(), f)
}

func (s *Server) servePlayback(w http.ResponseWriter, r *http.Request) {
	if !s.verified(w, r) {
		return
	}
	id := chi.URLParam(r, "id")
	if _, err := uuid.Parse(id); err != nil {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	key, err := s.store.PlaybackKey(r.Context(), id)
	if errors.Is(err, store.ErrNotFound) {
		writeErr(w, http.StatusNotFound, "not found")
		return
	} else if err != nil {
		s.fail(w, r, err)
		return
	}
	s.serveKey(w, r, key, "private, max-age=3600")
}

func (s *Server) servePoster(w http.ResponseWriter, r *http.Request) {
	if !s.verified(w, r) {
		return
	}
	key, err := s.store.PosterKey(r.Context(), chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	s.serveKey(w, r, key, "private, max-age=86400")
}

func (s *Server) serveFrameThumb(w http.ResponseWriter, r *http.Request) {
	if !s.verified(w, r) {
		return
	}
	id, err := strconv.ParseInt(chi.URLParam(r, "id"), 10, 64)
	if err != nil {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	key, err := s.store.FrameThumbKey(r.Context(), id)
	if err != nil {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	s.serveKey(w, r, key, "private, max-age=86400, immutable")
}
