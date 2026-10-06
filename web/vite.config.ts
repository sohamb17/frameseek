import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In development the Go API runs on :8080; Vite proxies API and media calls to it.
// VITE_BASE is "/frameseek/" for the GitHub Pages demo build (served from a sub-path).
export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8080",
      "/media": "http://localhost:8080",
      "/healthz": "http://localhost:8080",
    },
  },
});
