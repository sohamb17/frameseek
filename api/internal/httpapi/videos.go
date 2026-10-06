package httpapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"github.com/sohamb17/frameseek/api/internal/importer"
	"github.com/sohamb17/frameseek/api/internal/store"
)

var contentTypes = map[string]bool{"talk": true, "screencast": true, "demo": true, "other": true}

func (s *Server) me(w http.ResponseWriter, r *http.Request) {
	coll, err := s.store.DefaultCollection(r.Context(), owner(r))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"owner_id": owner(r), "default_collection_id": coll, "auth_mode": s.cfg.AuthMode,
		"limits": map[string]any{"max_upload_bytes": s.cfg.MaxUploadBytes, "containers": []string{"mp4", "mov", "mkv", "webm"}},
	})
}

func (s *Server) listCollections(w http.ResponseWriter, r *http.Request) {
	if _, err := s.store.DefaultCollection(r.Context(), owner(r)); err != nil {
		s.fail(w, r, err)
		return
	}
	rows, err := s.store.ListCollections(r.Context(), owner(r))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) createCollection(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name string `json:"name"`
	}
	if err := decode(r, &body); err != nil || strings.TrimSpace(body.Name) == "" || len(body.Name) > 100 {
		writeErr(w, http.StatusBadRequest, "name is required (max 100 chars)")
		return
	}
	row, err := s.store.CreateCollection(r.Context(), owner(r), strings.TrimSpace(body.Name))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusCreated, row)
}

// resolveCollection returns the requested collection if the caller owns it, else the default.
func (s *Server) resolveCollection(r *http.Request, requested string) (string, error) {
	if requested == "" {
		return s.store.DefaultCollection(r.Context(), owner(r))
	}
	ok, err := s.store.CollectionOwned(r.Context(), owner(r), requested)
	if err != nil {
		return "", err
	}
	if !ok {
		return "", store.ErrNotFound
	}
	return requested, nil
}

func (s *Server) decorateVideo(v store.Row) store.Row {
	id, _ := v["id"].(string)
	if v["poster_key"] != nil {
		v["poster_url"] = s.signer.Sign("/media/videos/" + id + "/poster")
	}
	if v["playback_key"] != nil || v["active_index_version"] != nil {
		v["playback_url"] = s.signer.Sign("/media/videos/" + id + "/playback")
	}
	delete(v, "poster_key")
	delete(v, "playback_key")
	return v
}

func (s *Server) listVideos(w http.ResponseWriter, r *http.Request) {
	coll := r.URL.Query().Get("collection_id")
	if coll != "" {
		if _, err := s.resolveCollection(r, coll); err != nil {
			s.fail(w, r, err)
			return
		}
	}
	rows, err := s.store.ListVideos(r.Context(), owner(r), coll)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	for _, v := range rows {
		s.decorateVideo(v)
		delete(v, "probe")
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) getVideo(w http.ResponseWriter, r *http.Request) {
	v, err := s.store.VideoDetail(r.Context(), owner(r), chi.URLParam(r, "id"))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, s.decorateVideo(v))
}

func (s *Server) videoTimeline(w http.ResponseWriter, r *http.Request) {
	rows, err := s.store.VideoTimeline(r.Context(), owner(r), chi.URLParam(r, "id"))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	for _, row := range rows {
		if fid, ok := row["thumb_frame_id"].(int64); ok {
			row["thumb_url"] = s.signer.Sign(fmt.Sprintf("/media/frames/%d/thumb", fid))
		}
	}
	writeJSON(w, http.StatusOK, rows)
}

func (s *Server) patchVideo(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Title       string `json:"title"`
		ContentType string `json:"content_type"`
	}
	if err := decode(r, &body); err != nil || (body.ContentType != "" && !contentTypes[body.ContentType]) {
		writeErr(w, http.StatusBadRequest, "invalid body")
		return
	}
	ok, err := s.store.UpdateVideoMeta(r.Context(), owner(r), chi.URLParam(r, "id"), body.Title, body.ContentType)
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

func (s *Server) deleteVideo(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	ok, err := s.store.DeleteVideo(r.Context(), owner(r), id)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if !ok {
		writeErr(w, http.StatusNotFound, "not found")
		return
	}
	// Artifacts are removed after the rows; a crash in between leaves orphans, never dangling rows.
	os.RemoveAll(filepath.Join(s.cfg.DataDir, "originals", id))
	os.RemoveAll(filepath.Join(s.cfg.DataDir, "videos", id))
	w.WriteHeader(http.StatusNoContent)
}

// sniffContainer identifies the container from magic bytes; extensions are not trusted.
func sniffContainer(head []byte) (ext string, ok bool) {
	switch {
	case len(head) >= 12 && string(head[4:8]) == "ftyp":
		if string(head[8:12]) == "qt  " {
			return ".mov", true
		}
		return ".mp4", true
	case len(head) >= 8 && (string(head[4:8]) == "moov" || string(head[4:8]) == "mdat" || string(head[4:8]) == "wide"):
		return ".mov", true
	case len(head) >= 4 && bytes.Equal(head[:4], []byte{0x1A, 0x45, 0xDF, 0xA3}):
		if bytes.Contains(head[:min(len(head), 64)], []byte("webm")) {
			return ".webm", true
		}
		return ".mkv", true
	}
	return "", false
}

type uploadMeta struct {
	Title, ContentType, Collection, License, Attribution, SourceURL string
	AutoProcess                                                    bool
}

// uploadVideo streams a multipart upload to disk while hashing it; nothing is buffered in memory.
func (s *Server) uploadVideo(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, s.cfg.MaxUploadBytes+(1<<20))
	mr, err := r.MultipartReader()
	if err != nil {
		writeErr(w, http.StatusBadRequest, "expected multipart/form-data")
		return
	}
	meta := uploadMeta{AutoProcess: true, ContentType: "other"}
	var tmpPath, filename, ext, hash string
	var size int64
	defer func() {
		if tmpPath != "" {
			os.Remove(tmpPath)
		}
	}()
	tmpDir := filepath.Join(s.cfg.DataDir, "uploads", "tmp")
	for {
		part, err := mr.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			writeErr(w, http.StatusBadRequest, "upload interrupted or too large")
			return
		}
		if part.FormName() != "file" {
			val, _ := io.ReadAll(io.LimitReader(part, 4096))
			v := strings.TrimSpace(string(val))
			switch part.FormName() {
			case "title":
				meta.Title = v
			case "content_type":
				meta.ContentType = v
			case "collection_id":
				meta.Collection = v
			case "license":
				meta.License = v
			case "attribution":
				meta.Attribution = v
			case "source_url":
				meta.SourceURL = v
			case "auto_process":
				meta.AutoProcess = v != "false"
			}
			continue
		}
		filename = filepath.Base(part.FileName())
		if err := os.MkdirAll(tmpDir, 0o755); err != nil {
			s.fail(w, r, err)
			return
		}
		f, err := os.CreateTemp(tmpDir, "upload-*.part")
		if err != nil {
			s.fail(w, r, err)
			return
		}
		tmpPath = f.Name()
		head := make([]byte, 64)
		n, _ := io.ReadFull(part, head)
		head = head[:n]
		var ok bool
		if ext, ok = sniffContainer(head); !ok {
			f.Close()
			writeErr(w, http.StatusUnsupportedMediaType, "unrecognized container; supported: MP4, MOV, MKV, WebM")
			return
		}
		h := sha256.New()
		h.Write(head)
		f.Write(head)
		written, err := io.Copy(io.MultiWriter(f, h), part)
		f.Close()
		if err != nil {
			var mbe *http.MaxBytesError
			if errors.As(err, &mbe) {
				writeErr(w, http.StatusRequestEntityTooLarge, fmt.Sprintf("file exceeds the %d MB limit", s.cfg.MaxUploadBytes>>20))
				return
			}
			writeErr(w, http.StatusBadRequest, "upload interrupted")
			return
		}
		size = written + int64(len(head))
		hash = hex.EncodeToString(h.Sum(nil))
	}
	if tmpPath == "" {
		writeErr(w, http.StatusBadRequest, "missing file part")
		return
	}
	if !contentTypes[meta.ContentType] {
		writeErr(w, http.StatusBadRequest, "content_type must be talk, screencast, demo or other")
		return
	}
	coll, err := s.resolveCollection(r, meta.Collection)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if meta.Title == "" {
		meta.Title = strings.TrimSuffix(filename, filepath.Ext(filename))
	}
	// Same bytes already in this collection: return the existing video (idempotent upload).
	if existing, err := s.store.FindByHash(r.Context(), coll, hash); err != nil {
		s.fail(w, r, err)
		return
	} else if existing != "" {
		v, err := s.store.GetVideo(r.Context(), owner(r), existing)
		if err != nil {
			s.fail(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"video": s.decorateVideo(v), "deduplicated": true})
		return
	}
	id := uuid.NewString()
	key := fmt.Sprintf("originals/%s/source%s", id, ext)
	dst := filepath.Join(s.cfg.DataDir, key)
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		s.fail(w, r, err)
		return
	}
	if err := os.Rename(tmpPath, dst); err != nil {
		s.fail(w, r, err)
		return
	}
	tmpPath = ""
	err = s.store.CreateVideo(r.Context(), store.NewVideo{
		ID: id, Owner: owner(r), Collection: coll, Title: meta.Title, ContentType: meta.ContentType,
		SourceKind: "upload", SourceURL: meta.SourceURL, License: meta.License, Attribution: meta.Attribution,
		Filename: filename, OriginalKey: key, ContentHash: hash, Size: size, Status: "registered",
	})
	if err != nil {
		os.RemoveAll(filepath.Dir(dst))
		s.fail(w, r, err)
		return
	}
	resp := map[string]any{"deduplicated": false}
	if meta.AutoProcess {
		job, _, err := s.store.CreateProcessJob(r.Context(), owner(r), id, false)
		if err != nil {
			s.fail(w, r, err)
			return
		}
		resp["job"] = job
	}
	v, err := s.store.GetVideo(r.Context(), owner(r), id)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	resp["video"] = s.decorateVideo(v)
	writeJSON(w, http.StatusCreated, resp)
}

func (s *Server) importVideo(w http.ResponseWriter, r *http.Request) {
	var body struct {
		URL          string `json:"url"`
		Title        string `json:"title"`
		ContentType  string `json:"content_type"`
		CollectionID string `json:"collection_id"`
		License      string `json:"license"`
		Attribution  string `json:"attribution"`
	}
	if err := decode(r, &body); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid body")
		return
	}
	if body.ContentType == "" {
		body.ContentType = "other"
	}
	if !contentTypes[body.ContentType] {
		writeErr(w, http.StatusBadRequest, "content_type must be talk, screencast, demo or other")
		return
	}
	u, err := importer.Validate(body.URL, s.cfg.AllowPrivateURL)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	coll, err := s.resolveCollection(r, body.CollectionID)
	if err != nil {
		s.fail(w, r, err)
		return
	}
	if body.Title == "" {
		body.Title = filepath.Base(u.Path)
	}
	id := uuid.NewString()
	own := owner(r)
	if err := s.store.CreateVideo(r.Context(), store.NewVideo{
		ID: id, Owner: own, Collection: coll, Title: body.Title, ContentType: body.ContentType, SourceKind: "url",
		SourceURL: u.String(), License: body.License, Attribution: body.Attribution, Status: "importing",
	}); err != nil {
		s.fail(w, r, err)
		return
	}
	go s.runImport(id, own, coll, u.String())
	v, _ := s.store.GetVideo(r.Context(), own, id)
	writeJSON(w, http.StatusAccepted, map[string]any{"video": s.decorateVideo(v)})
}

func (s *Server) runImport(id, own, coll, rawURL string) {
	ctx := s.bgCtx
	res, err := s.importer.Download(ctx, rawURL, filepath.Join(s.cfg.DataDir, "uploads", "tmp"))
	if err != nil {
		s.log.Warn("import failed", "video", id, "err", err)
		s.store.FailVideo(ctx, id, "import failed: "+err.Error()) //nolint:errcheck
		return
	}
	defer os.Remove(res.Path)
	f, err := os.Open(res.Path)
	if err != nil {
		s.store.FailVideo(ctx, id, err.Error()) //nolint:errcheck
		return
	}
	head := make([]byte, 64)
	n, _ := io.ReadFull(f, head)
	f.Close()
	ext, ok := sniffContainer(head[:n])
	if !ok {
		s.store.FailVideo(ctx, id, "unrecognized container; supported: MP4, MOV, MKV, WebM") //nolint:errcheck
		return
	}
	if dup, _ := s.store.FindByHash(ctx, coll, res.SHA256); dup != "" {
		s.store.FailVideo(ctx, id, "this file is already in the library") //nolint:errcheck
		return
	}
	key := fmt.Sprintf("originals/%s/source%s", id, ext)
	dst := filepath.Join(s.cfg.DataDir, key)
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err == nil {
		err = os.Rename(res.Path, dst)
	}
	if err != nil {
		s.store.FailVideo(ctx, id, err.Error()) //nolint:errcheck
		return
	}
	if err := s.store.CompleteImport(ctx, id, key, res.SHA256, res.Size); err != nil {
		s.store.FailVideo(ctx, id, err.Error()) //nolint:errcheck
		return
	}
	if _, _, err := s.store.CreateProcessJob(ctx, own, id, false); err != nil {
		s.store.FailVideo(ctx, id, err.Error()) //nolint:errcheck
	}
	s.log.Info("import complete", "video", id, "bytes", res.Size)
}

func (s *Server) processVideo(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Force bool `json:"force"`
	}
	if r.ContentLength > 0 {
		if err := decode(r, &body); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid body")
			return
		}
	}
	job, dedup, err := s.store.CreateProcessJob(r.Context(), owner(r), chi.URLParam(r, "id"), body.Force)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			s.fail(w, r, err)
			return
		}
		writeErr(w, http.StatusConflict, err.Error())
		return
	}
	status := http.StatusCreated
	if dedup {
		status = http.StatusOK
	}
	writeJSON(w, status, map[string]any{"job": job, "deduplicated": dedup})
}

func (s *Server) getJob(w http.ResponseWriter, r *http.Request) {
	job, err := s.store.GetJob(r.Context(), owner(r), chi.URLParam(r, "id"))
	if err != nil {
		s.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, job)
}
