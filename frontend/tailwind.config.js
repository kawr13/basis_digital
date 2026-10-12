/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // тёмно-синяя (сине-графитовая) база кабинета: 900 — почти чёрно-синий
        ink: {
          900: "#070d1a",
          800: "#0d1728",
          700: "#13233c",
          600: "#1d3050",
          500: "#2c4468",
          400: "#7d94b5",
          300: "#b3c6e0",
          100: "#eaf1fd",
        },
        // ключевые акценты: синий + голубой (кнопки, ссылки, графики, подсветка)
        accent: "#4f8cff",
        accent2: "#38bdf8",
        // тёплый акцент — сигнальные состояния («режим настройки», выбранные фильтры)
        amber2: "#f0a02c",
        // семантика не тронута: зелёный / жёлтый / красный различимы и на синем
        ok: "#3fb27f",
        warn: "#e8b13c",
        crit: "#e0574c",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        num: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(179,198,224,.08) inset, 0 12px 32px -14px rgba(2,7,18,.85)",
      },
    },
  },
  plugins: [],
};
