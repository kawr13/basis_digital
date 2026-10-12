import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Фронт ходит в бэкенд через относительный путь /api — в dev через прокси,
// в проде через nginx. Тогда refresh-cookie живёт в одном origin и SameSite не мешает.
const proxy = {
  "/api": { target: process.env.VITE_API_PROXY || "http://127.0.0.1:8000", changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  build: { outDir: "dist", sourcemap: false },
});
