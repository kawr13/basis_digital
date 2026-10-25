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
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        // Реакт — отдельным чанком: он не меняется от релиза к релизу, поэтому
        // после выката браузер переиспользует его из кэша и догружает только
        // маленький чанк приложения да чанк нужной страницы (см. п.7 TASKS.md).
        // chunkSizeWarningLimit намеренно НЕ поднимаем: пусть сборка честно
        // ругается, если какая-то страница снова разрастётся.
        manualChunks: {
          "react-vendor": ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
});
