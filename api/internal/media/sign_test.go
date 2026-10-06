package media

import (
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestSignVerify(t *testing.T) {
	now := time.Date(2026, 10, 5, 12, 0, 0, 0, time.UTC)
	s := &Signer{Secret: []byte("k"), TTL: time.Hour, Now: func() time.Time { return now }}
	signed := s.Sign("/media/videos/abc/playback")
	path, qs, _ := strings.Cut(signed, "?")
	q, _ := url.ParseQuery(qs)
	if !s.Verify(path, q.Get("exp"), q.Get("sig")) {
		t.Fatal("fresh signature should verify")
	}
	if s.Verify("/media/videos/other/playback", q.Get("exp"), q.Get("sig")) {
		t.Fatal("signature must be bound to the path")
	}
	now = now.Add(3 * time.Hour)
	if s.Verify(path, q.Get("exp"), q.Get("sig")) {
		t.Fatal("expired signature must fail")
	}
}
