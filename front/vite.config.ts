/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API_BASE = process.env.VITE_API_BASE || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
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
