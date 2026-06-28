/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API_BASE = process.env.VITE_API_BASE || "http://localhost:8000";
const OPS_BASE = process.env.VITE_OPS_BASE || "http://localhost:8001";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // dev-only: same-origin /api -> the read-only FastAPI (014). No CORS change to the API.
      "/api": { target: API_BASE, changeOrigin: true },
      // dev-only: same-origin /ops -> the write ops service (024). Separate app/port from 014.
      "/ops": { target: OPS_BASE, changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    css: false,
  },
});
