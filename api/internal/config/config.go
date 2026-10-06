// Package config reads the API's settings from the environment.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Addr            string
	DatabaseURL     string
	DataDir         string
	ConfigDir       string
	WebDir          string
	MLURL           string
	InternalToken   string
	MediaSecret     string
	AuthMode        string            // "none" (single demo owner) or "token"
	Tokens          map[string]string // bearer token -> owner id
	DefaultOwner    string
	MaxUploadBytes  int64
	MaxImportBytes  int64
	SearchTimeout   time.Duration
	ConvTimeout     time.Duration
	ImportTimeout   time.Duration
	AllowPrivateURL bool // tests only: allow importing from private addresses
	MediaURLTTL     time.Duration
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int64) int64 {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			return n
		}
	}
	return def
}

func Load() (Config, error) {
	c := Config{
		Addr:            env("FRAMESEEK_ADDR", ":8080"),
		DatabaseURL:     env("FRAMESEEK_DATABASE_URL", "postgres://frameseek:frameseek@localhost:5432/frameseek"),
		DataDir:         env("FRAMESEEK_DATA_DIR", "./data"),
		ConfigDir:       env("FRAMESEEK_CONFIG_DIR", "../configs"),
		WebDir:          env("FRAMESEEK_WEB_DIR", "../web/dist"),
		MLURL:           strings.TrimRight(env("FRAMESEEK_ML_URL", "http://localhost:8000"), "/"),
		InternalToken:   env("FRAMESEEK_INTERNAL_TOKEN", "dev-internal-token"),
		MediaSecret:     env("FRAMESEEK_MEDIA_SECRET", "dev-media-secret-change-me"),
		AuthMode:        env("FRAMESEEK_AUTH_MODE", "none"),
		DefaultOwner:    env("FRAMESEEK_DEFAULT_OWNER", "demo"),
		MaxUploadBytes:  envInt("FRAMESEEK_MAX_UPLOAD_BYTES", 2<<30), // 2 GiB (blueprint cap)
		MaxImportBytes:  envInt("FRAMESEEK_MAX_IMPORT_BYTES", 2<<30),
		SearchTimeout:   time.Duration(envInt("FRAMESEEK_SEARCH_TIMEOUT_MS", 15000)) * time.Millisecond,
		ConvTimeout:     time.Duration(envInt("FRAMESEEK_CONVERSATION_TIMEOUT_MS", 30000)) * time.Millisecond,
		ImportTimeout:   time.Duration(envInt("FRAMESEEK_IMPORT_TIMEOUT_S", 600)) * time.Second,
		AllowPrivateURL: env("FRAMESEEK_ALLOW_PRIVATE_URLS", "false") == "true",
		MediaURLTTL:     6 * time.Hour,
		Tokens:          map[string]string{},
	}
	if c.AuthMode != "none" && c.AuthMode != "token" {
		return c, fmt.Errorf("FRAMESEEK_AUTH_MODE must be none or token")
	}
	// FRAMESEEK_TOKENS="demo:secret1,alice:secret2"
	for _, pair := range strings.Split(env("FRAMESEEK_TOKENS", ""), ",") {
		owner, tok, ok := strings.Cut(strings.TrimSpace(pair), ":")
		if ok && owner != "" && tok != "" {
			c.Tokens[tok] = owner
		}
	}
	if c.AuthMode == "token" && len(c.Tokens) == 0 {
		return c, fmt.Errorf("FRAMESEEK_AUTH_MODE=token needs FRAMESEEK_TOKENS")
	}
	return c, nil
}
