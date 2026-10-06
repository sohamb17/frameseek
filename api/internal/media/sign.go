// Package media signs and verifies expiring media URLs, so <video src> and <img src> work
// without exposing storage paths or long-lived credentials.
package media

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strconv"
	"time"
)

type Signer struct {
	Secret []byte
	TTL    time.Duration
	Now    func() time.Time
}

func (s *Signer) mac(path string, exp int64) string {
	m := hmac.New(sha256.New, s.Secret)
	fmt.Fprintf(m, "%s|%d", path, exp)
	return hex.EncodeToString(m.Sum(nil))[:32]
}

func (s *Signer) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

// Sign returns path?exp=..&sig=.. . Expiry is rounded up to the hour so URLs stay cacheable.
func (s *Signer) Sign(path string) string {
	exp := s.now().Add(s.TTL).Truncate(time.Hour).Add(time.Hour).Unix()
	return fmt.Sprintf("%s?exp=%d&sig=%s", path, exp, s.mac(path, exp))
}

func (s *Signer) Verify(path, expStr, sig string) bool {
	exp, err := strconv.ParseInt(expStr, 10, 64)
	if err != nil || s.now().Unix() > exp {
		return false
	}
	return hmac.Equal([]byte(sig), []byte(s.mac(path, exp)))
}
