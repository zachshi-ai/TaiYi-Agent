import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build a single-page app into ../web/dist so the taiyi gateway can serve it
// same-origin (no CORS, no separate static host). Dev server proxies /v1 and
// /metrics to the running gateway for local development.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://127.0.0.1:8080",
      "/metrics": "http://127.0.0.1:8080",
      "/healthz": "http://127.0.0.1:8080",
    },
  },
});
