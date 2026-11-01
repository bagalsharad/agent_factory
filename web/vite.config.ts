/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

const backend = process.env.AF_BACKEND_URL ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: Object.fromEntries(["/api", "/health", "/ready", "/docs", "/openapi.json"].map((p) => [p, backend])),
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
});
