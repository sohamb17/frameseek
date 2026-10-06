package httpapi

import "testing"

func TestSniffContainer(t *testing.T) {
	cases := map[string]struct {
		head []byte
		ext  string
		ok   bool
	}{
		"mp4":  {append([]byte{0, 0, 0, 0x20}, []byte("ftypisom0000")...), ".mp4", true},
		"mov":  {append([]byte{0, 0, 0, 0x14}, []byte("ftypqt  0000")...), ".mov", true},
		"webm": {append([]byte{0x1A, 0x45, 0xDF, 0xA3, 0x9F, 0x42, 0x86, 0x81, 0x01, 0x42, 0x82, 0x84}, []byte("webm")...), ".webm", true},
		"mkv":  {append([]byte{0x1A, 0x45, 0xDF, 0xA3, 0x9F, 0x42, 0x82, 0x88}, []byte("matroska")...), ".mkv", true},
		"html": {[]byte("<!doctype html><html>"), "", false},
		"png":  {[]byte{0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A, 0, 0, 0, 0}, "", false},
	}
	for name, c := range cases {
		ext, ok := sniffContainer(c.head)
		if ext != c.ext || ok != c.ok {
			t.Errorf("%s: got (%q,%v) want (%q,%v)", name, ext, ok, c.ext, c.ok)
		}
	}
}

func TestSearchValidation(t *testing.T) {
	ok := searchRequest{Query: "  cache stale  "}
	if msg := ok.validate(); msg != "" || ok.K != 10 || ok.Query != "cache stale" {
		t.Fatalf("defaults not applied: %q %+v", msg, ok)
	}
	bad := []searchRequest{
		{Query: ""},
		{Query: "x", K: 99},
		{Query: "x", Ranker: "Z"},
		{Query: "x", VideoIDs: []string{"not-a-uuid"}},
		{Query: "x", ContentTypes: []string{"movie"}},
	}
	for i, b := range bad {
		if b.validate() == "" {
			t.Errorf("case %d should fail validation", i)
		}
	}
}
