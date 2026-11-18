/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// В разработке API — FastAPI на :8000; в сборке дашборд отдаёт тот же сервер, что и API.
const api = "http://127.0.0.1:8000";
const proxied = ["/chat", "/evals", "/meta", "/health"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(proxied.map((p) => [p, { target: api, changeOrigin: true }])),
  },
  build: { outDir: "dist", sourcemap: false },
  test: { environment: "node" },
});
