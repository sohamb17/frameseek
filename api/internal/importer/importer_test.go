package importer

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"testing"
	"time"
)

func TestIsPublic(t *testing.T) {
	blocked := []string{"127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
		"100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "224.0.0.1", "64:ff9b::7f00:1"}
	for _, s := range blocked {
		if IsPublic(netip.MustParseAddr(s)) {
			t.Errorf("%s should be blocked", s)
		}
	}
	for _, s := range []string{"8.8.8.8", "151.101.1.1", "2606:4700::1111"} {
		if !IsPublic(netip.MustParseAddr(s)) {
			t.Errorf("%s should be allowed", s)
		}
	}
}

func TestValidateRejectsNonHTTPS(t *testing.T) {
	for _, u := range []string{"http://example.com/a.mp4", "file:///etc/passwd", "ftp://x/y", "https://user:pw@x.com/a"} {
		if _, err := Validate(u, false); err == nil {
			t.Errorf("%s should be rejected", u)
		}
	}
}

func TestDownloadBlocksLoopback(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("x")) }))
	defer srv.Close()
	im := New(1<<20, 5*time.Second, false)
	_, err := im.Download(context.Background(), srv.URL+"/video.mp4", t.TempDir())
	if !errors.Is(err, ErrBlockedAddress) {
		t.Fatalf("expected blocked address, got %v", err)
	}
}

func TestDownloadEnforcesSizeAndType(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/page":
			w.Header().Set("Content-Type", "text/html")
			w.Write([]byte("<html></html>"))
		case "/redirect":
			http.Redirect(w, r, "/big", http.StatusFound)
		default:
			w.Header().Set("Content-Type", "video/mp4")
			w.Write(make([]byte, 4096))
		}
	}))
	defer srv.Close()
	im := New(1024, 5*time.Second, true) // AllowPrivate so the local test server is reachable
	if _, err := im.Download(context.Background(), srv.URL+"/page", t.TempDir()); err == nil {
		t.Fatal("html page should be rejected")
	}
	if _, err := im.Download(context.Background(), srv.URL+"/redirect", t.TempDir()); err == nil {
		t.Fatal("oversized download should be rejected")
	}
	im.MaxBytes = 1 << 20
	res, err := im.Download(context.Background(), srv.URL+"/ok.mp4", t.TempDir())
	if err != nil || res.Size != 4096 || len(res.SHA256) != 64 {
		t.Fatalf("unexpected result %+v %v", res, err)
	}
}
