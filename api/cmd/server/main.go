// Command server runs the FrameSeek public API.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/sohamb17/frameseek/api/internal/config"
	"github.com/sohamb17/frameseek/api/internal/httpapi"
	"github.com/sohamb17/frameseek/api/internal/migrate"
	"github.com/sohamb17/frameseek/api/internal/store"
)

func main() {
	log := slog.New(slog.NewTextHandler(os.Stdout, nil))
	if err := run(log); err != nil {
		log.Error("fatal", "err", err)
		os.Exit(1)
	}
}

func run(log *slog.Logger) error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	var pool *pgxpool.Pool
	for attempt := 1; ; attempt++ { // the database container may still be starting
		pool, err = pgxpool.New(ctx, cfg.DatabaseURL)
		if err == nil {
			if err = pool.Ping(ctx); err == nil {
				break
			}
			pool.Close()
		}
		if attempt >= 30 {
			return err
		}
		log.Info("waiting for database", "attempt", attempt)
		time.Sleep(2 * time.Second)
	}
	defer pool.Close()

	applied, err := migrate.Up(ctx, pool)
	if err != nil {
		return err
	}
	log.Info("migrations", "applied", applied)

	st := &store.Store{Pool: pool, ConfigDir: cfg.ConfigDir}
	if n, err := st.FailInterruptedImports(ctx); err == nil && n > 0 {
		log.Warn("marked interrupted imports as failed", "count", n)
	}
	srv := &http.Server{
		Addr:              cfg.Addr,
		Handler:           httpapi.New(ctx, cfg, st, log).Routes(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		srv.Shutdown(shutdown) //nolint:errcheck
	}()
	log.Info("listening", "addr", cfg.Addr, "auth", cfg.AuthMode)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
