/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Фирменная палитра cab.basisport.ru — снята с computed styles живого сайта
        // и из /css/main.css. Использовать ровно её, свои оттенки не выдумывать.
        brand: {
          navy: "#1b3360", //  nav.navbar-mrp { background-color } — главный тёмно-синий
          sky: "#2fbaec", //   .nav-custom:hover — фирменный голубой
          mid: "#337ab7", //   bootstrap .panel-heading — заголовки панелей
          active: "#428bca", // bootstrap — активные пункты
          focus: "#66afe9", //  bootstrap — фокус полей
        },
        // тёмно-синяя база кабинета: пересчитана вокруг шапочного navy #1b3360
        // (ink-600 === brand.navy), 900 — почти чёрно-синий, 100 — почти белый
        ink: {
          900: "#060b16",
          800: "#0b1424",
          700: "#12203a",
          600: "#1b3360",
          500: "#2a4a80",
          400: "#7f97bd",
          300: "#b9c9e2",
          100: "#eef3fb",
        },
        // ключевые акценты: фирменный голубой сайта + светлый голубой фокуса
        accent: "#2fbaec",
        accent2: "#66afe9",
        // тёплый акцент — сигнальный (столбцы графика, режим настройки): не трогали
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
        // «стеклянная» плашка: внутренняя верхняя подсветка + мягкая внешняя тень
        panel:
          "0 1px 0 0 rgba(255,255,255,.18) inset, 0 18px 40px -22px rgba(3,8,18,.75), 0 2px 8px -4px rgba(6,11,22,.55)",
      },
    },
  },
  plugins: [],
};
