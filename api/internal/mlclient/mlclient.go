// Package mlclient calls the private Python service with a deadline on every request.
package mlclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	BaseURL string
	Token   string
	HTTP    *http.Client
}

var ErrTimeout = errors.New("ml service deadline exceeded")

type HTTPError struct {
	Status int
	Body   string
}

func (e *HTTPError) Error() string { return fmt.Sprintf("ml service returned %d: %s", e.Status, e.Body) }

func New(base, token string) *Client {
	return &Client{BaseURL: base, Token: token, HTTP: &http.Client{Transport: &http.Transport{Proxy: nil, MaxIdleConnsPerHost: 16}}}
}

// Post sends body as JSON to path and decodes the JSON response into out, within timeout.
func (c *Client) Post(ctx context.Context, path string, timeout time.Duration, body, out any) error {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	raw, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, bytes.NewReader(raw))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-FrameSeek-Internal", c.Token)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return ErrTimeout
		}
		return err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return err
	}
	if resp.StatusCode >= 300 {
		return &HTTPError{Status: resp.StatusCode, Body: string(data)}
	}
	return json.Unmarshal(data, out)
}

func (c *Client) Healthy(ctx context.Context) (map[string]any, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+"/healthz", nil)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var out map[string]any
	return out, json.NewDecoder(resp.Body).Decode(&out)
}
