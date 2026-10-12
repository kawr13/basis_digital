/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#0d1117",
          800: "#141a22",
          700: "#1b232d",
          600: "#25303d",
          500: "#384554",
          400: "#6b7a8d",
          300: "#9aa8b8",
          100: "#e7edf4",
        },
        amber2: "#f0a02c",
        ok: "#3fb27f",
        warn: "#e8b13c",
        crit: "#e0574c",
        teal2: "#3f9fae",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        num: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,.04) inset, 0 8px 24px -12px rgba(0,0,0,.6)",
      },
    },
  },
  plugins: [],
};
