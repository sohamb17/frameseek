// Package importer downloads media from a user-supplied HTTPS URL without letting the URL
// reach private, loopback, link-local (cloud metadata) or other internal addresses.
//
// The check happens in the dialer, after DNS resolution, and the connection is made to the
// exact IP that was checked; this closes the DNS-rebinding gap. Every redirect goes through the
// same dialer, so a public URL that redirects to 169.254.169.254 is refused too.
package importer

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"strings"
	"time"
)

var ErrBlockedAddress = errors.New("destination address is not allowed")

type Importer struct {
	MaxBytes     int64
	Timeout      time.Duration
	AllowPrivate bool // tests only
	MaxRedirects int
	client       *http.Client
}

func New(maxBytes int64, timeout time.Duration, allowPrivate bool) *Importer {
	im := &Importer{MaxBytes: maxBytes, Timeout: timeout, AllowPrivate: allowPrivate, MaxRedirects: 5}
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	transport := &http.Transport{
		Proxy: nil, // never route user URLs through an ambient proxy
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(addr)
			if err != nil {
				return nil, err
			}
			ips, err := net.DefaultResolver.LookupNetIP(ctx, "ip", host)
			if err != nil {
				return nil, err
			}
			for _, ip := range ips {
				if !im.AllowPrivate && !IsPublic(ip) {
					return nil, fmt.Errorf("%w: %s resolves to %s", ErrBlockedAddress, host, ip)
				}
			}
			if len(ips) == 0 {
				return nil, fmt.Errorf("no addresses for %s", host)
			}
			return dialer.DialContext(ctx, network, net.JoinHostPort(ips[0].String(), port))
		},
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 20 * time.Second,
		MaxIdleConns:          4,
	}
	im.client = &http.Client{
		Transport: transport,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= im.MaxRedirects {
				return fmt.Errorf("too many redirects")
			}
			return checkScheme(req.URL, im.AllowPrivate)
		},
	}
	return im
}

// IsPublic reports whether ip is a globally routable unicast address.
func IsPublic(ip netip.Addr) bool {
	ip = ip.Unmap()
	if !ip.IsValid() || ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() ||
		ip.IsLinkLocalMulticast() || ip.IsInterfaceLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified() {
		return false
	}
	for _, p := range blockedPrefixes {
		if p.Contains(ip) {
			return false
		}
	}
	return true
}

var blockedPrefixes = []netip.Prefix{
	netip.MustParsePrefix("0.0.0.0/8"),
	netip.MustParsePrefix("100.64.0.0/10"), // carrier-grade NAT
	netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("192.0.2.0/24"),
	netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("198.51.100.0/24"),
	netip.MustParsePrefix("203.0.113.0/24"),
	netip.MustParsePrefix("240.0.0.0/4"),
	netip.MustParsePrefix("64:ff9b::/96"), // NAT64 can reach IPv4 internals
	netip.MustParsePrefix("2001:db8::/32"),
}

func checkScheme(u *url.URL, allowHTTP bool) error {
	if u.Scheme == "https" || (allowHTTP && u.Scheme == "http") {
		return nil
	}
	return fmt.Errorf("only https:// media URLs are supported")
}

func Validate(raw string, allowHTTP bool) (*url.URL, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Host == "" {
		return nil, fmt.Errorf("invalid URL")
	}
	if u.User != nil {
		return nil, fmt.Errorf("URLs with credentials are not supported")
	}
	return u, checkScheme(u, allowHTTP)
}

type Result struct {
	Path     string
	Size     int64
	SHA256   string
	Filename string
}

// Download streams the body to a temp file in dir, hashing as it goes, and enforces MaxBytes.
func (im *Importer) Download(ctx context.Context, rawURL, dir string) (*Result, error) {
	u, err := Validate(rawURL, im.AllowPrivate)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, im.Timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "FrameSeek-importer/0.1")
	resp, err := im.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("remote server answered %s", resp.Status)
	}
	ct, _, _ := mime.ParseMediaType(resp.Header.Get("Content-Type"))
	if strings.HasPrefix(ct, "text/") || ct == "application/json" {
		return nil, fmt.Errorf("the URL returned %q, not a media file (website extraction is not supported; use a direct media link)", ct)
	}
	if resp.ContentLength > im.MaxBytes {
		return nil, fmt.Errorf("file is %d MB; the limit is %d MB", resp.ContentLength>>20, im.MaxBytes>>20)
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	f, err := os.CreateTemp(dir, "import-*.part")
	if err != nil {
		return nil, err
	}
	h := sha256.New()
	n, err := io.Copy(io.MultiWriter(f, h), io.LimitReader(resp.Body, im.MaxBytes+1))
	f.Close()
	if err == nil && n > im.MaxBytes {
		err = fmt.Errorf("download exceeded the %d MB limit", im.MaxBytes>>20)
	}
	if err != nil {
		os.Remove(f.Name())
		return nil, err
	}
	name := u.Path[strings.LastIndex(u.Path, "/")+1:]
	return &Result{Path: f.Name(), Size: n, SHA256: hex.EncodeToString(h.Sum(nil)), Filename: name}, nil
}
