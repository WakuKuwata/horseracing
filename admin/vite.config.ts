/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API_BASE = process.env.VITE_API_BASE || "http://localhost:8000";

// Feature 051: ADMIN console — LOCALHOST ONLY (auth is explicitly deferred; the structural guard
// is that this server binds 127.0.0.1 and must never be exposed. Do not change host to 0.0.0.0.)
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5175,
    proxy: {
      // dev-only: same-origin /api -> the read-only FastAPI (014). No CORS change to the API.
      "/api": { target: API_BASE, changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    css: false,
  },
});
