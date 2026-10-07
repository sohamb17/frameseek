// Package httpapi is FrameSeek's public HTTP API: input contracts, authorization boundary,
// durable job creation, deadlines on ML calls, and signed media URLs.
package httpapi

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"

	"github.com/sohamb17/frameseek/api/internal/config"
	"github.com/sohamb17/frameseek/api/internal/importer"
	"github.com/sohamb17/frameseek/api/internal/media"
	"github.com/sohamb17/frameseek/api/internal/mlclient"
	"github.com/sohamb17/frameseek/api/internal/store"
)

type Server struct {
	cfg      config.Config
	store    *store.Store
	ml       *mlclient.Client
	signer   *media.Signer
	importer *importer.Importer
	log      *slog.Logger
	bgCtx    context.Context
}

func New(ctx context.Context, cfg config.Config, st *store.Store, log *slog.Logger) *Server {
	return &Server{
		cfg:      cfg,
		store:    st,
		ml:       mlclient.New(cfg.MLURL, cfg.InternalToken),
		signer:   &media.Signer{Secret: []byte(cfg.MediaSecret), TTL: cfg.MediaURLTTL},
		importer: importer.New(cfg.MaxImportBytes, cfg.ImportTimeout, cfg.AllowPrivateURL),
		log:      log,
		bgCtx:    ctx,
	}
}

type ctxKey int

const ownerKey ctxKey = 1

func owner(r *http.Request) string { return r.Context().Value(ownerKey).(string) }

// auth resolves the owner for every /api request. Body fields never choose the owner.
func (s *Server) auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var who string
		// Internal calls (the Python conversation retriever) act on behalf of an owner the API
		// itself passed to Python, authenticated by the shared internal token.
		if tok := r.Header.Get("X-FrameSeek-Internal"); tok != "" {
			if subtle.ConstantTimeCompare([]byte(tok), []byte(s.cfg.InternalToken)) != 1 {
				writeErr(w, http.StatusUnauthorized, "bad internal token")
				return
			}
			who = r.Header.Get("X-FrameSeek-Owner")
		} else if s.cfg.AuthMode == "none" {
			who = s.cfg.DefaultOwner
		} else {
			bearer := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
			who = s.cfg.Tokens[bearer]
		}
		if who == "" {
			writeErr(w, http.StatusUnauthorized, "authentication required")
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ownerKey, who)))
	})
}

func (s *Server) Routes() http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.RequestID, middleware.RealIP, s.requestLog, middleware.Recoverer)

	r.Get("/healthz", s.health)
	r.Route("/api", func(r chi.Router) {
		r.Use(s.auth)
		r.Get("/me", s.me)
		r.Get("/collections", s.listCollections)
		r.Post("/collections", s.createCollection)

		r.Get("/videos", s.listVideos)
		r.Post("/videos", s.uploadVideo)
		r.Post("/videos/import", s.importVideo)
		r.Get("/videos/{id}", s.getVideo)
		r.Patch("/videos/{id}", s.patchVideo)
		r.Delete("/videos/{id}", s.deleteVideo)
		r.Get("/videos/{id}/timeline", s.videoTimeline)
		r.Post("/videos/{id}/process", s.processVideo)
		r.Get("/jobs/{id}", s.getJob)

		r.Post("/search", s.search)
		r.Post("/feedback", s.feedback)
		r.Get("/feedback/stats", s.feedbackStats)

		r.Get("/bookmarks", s.listBookmarks)
		r.Post("/bookmarks", s.createBookmark)
		r.Delete("/bookmarks/{id}", s.deleteBookmark)

		r.Get("/conversations", s.listConversations)
		r.Post("/conversations", s.createConversation)
		r.Get("/conversations/{id}", s.getConversation)
		r.Post("/conversations/{id}/messages", s.postMessage)

		r.Get("/eval/queries", s.listEvalQueries)
		r.Post("/eval/queries", s.createEvalQuery)
		r.Put("/eval/queries/{id}", s.updateEvalQuery)
		r.Post("/eval/queries/{id}/reject", s.rejectEvalQuery)
		r.Delete("/eval/queries/{id}", s.deleteEvalQuery)
		r.Get("/eval/report", s.evalReport)
	})
	// Media is authorized by an expiring HMAC signature in the URL (see media.Signer).
	r.Get("/media/videos/{id}/playback", s.servePlayback)
	r.Get("/media/videos/{id}/poster", s.servePoster)
	r.Get("/media/frames/{id}/thumb", s.serveFrameThumb)

	r.NotFound(s.spa)
	return r
}

func (s *Server) requestLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
		next.ServeHTTP(ww, r)
		if strings.HasPrefix(r.URL.Path, "/media/") || strings.HasPrefix(r.URL.Path, "/assets/") {
			return
		}
		s.log.Info("http", "method", r.Method, "path", r.URL.Path, "status", ww.Status(),
			"ms", time.Since(start).Milliseconds(), "req", middleware.GetReqID(r.Context()))
	})
}

// spa serves the built React app and falls back to index.html for client-side routes.
func (s *Server) spa(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/api/") {
		writeErr(w, http.StatusNotFound, "no such endpoint")
		return
	}
	root, _ := filepath.Abs(s.cfg.WebDir)
	p := filepath.Join(root, filepath.Clean("/"+r.URL.Path))
	if st, err := os.Stat(p); err == nil && !st.IsDir() {
		if strings.Contains(r.URL.Path, "/assets/") {
			w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
		}
		http.ServeFile(w, r, p)
		return
	}
	index := filepath.Join(root, "index.html")
	if _, err := os.Stat(index); err != nil {
		writeErr(w, http.StatusNotFound, "web UI not built; run `npm run build` in web/ or use `npm run dev`")
		return
	}
	w.Header().Set("Cache-Control", "no-cache")
	http.ServeFile(w, r, index)
}

func (s *Server) health(w http.ResponseWriter, r *http.Request) {
	out := map[string]any{"api": "ok"}
	if err := s.store.Pool.Ping(r.Context()); err != nil {
		out["db"] = err.Error()
	} else {
		out["db"] = "ok"
	}
	if h, err := s.ml.Healthy(r.Context()); err != nil {
		out["ml"] = "unreachable"
	} else {
		out["ml"] = h
	}
	writeJSON(w, http.StatusOK, out)
}

// ----------------------------------------------------------------- helpers

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v) //nolint:errcheck
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func (s *Server) fail(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, store.ErrNotFound):
		writeErr(w, http.StatusNotFound, "not found")
	case errors.Is(err, mlclient.ErrTimeout):
		writeErr(w, http.StatusGatewayTimeout, "the search service did not answer in time")
	default:
		var he *mlclient.HTTPError
		if errors.As(err, &he) {
			writeErr(w, http.StatusBadGateway, "ml service error: "+truncate(he.Body, 300))
			return
		}
		s.log.Error("request failed", "path", r.URL.Path, "err", err)
		writeErr(w, http.StatusInternalServerError, "internal error")
	}
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func decode(r *http.Request, v any) error {
	dec := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 1<<20))
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}
