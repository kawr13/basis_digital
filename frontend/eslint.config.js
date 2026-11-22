// Плоский конфиг ESLint 9 — п. 31 TASKS2.md: ESLint + react-hooks.
// Правила подобраны так, чтобы линтер ловил настоящие баги (нарушения
// правил хуков, неполные зависимости эффектов) и не шумел на стиле.
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  // Артефакты сборки Vite линтить бессмысленно.
  { ignores: ['dist'] },

  // Базовый набор: рекомендуемые правила JS + TypeScript (включая TS-файлы).
  js.configs.recommended,
  ...tseslint.configs.recommended,

  // Конфиги сборки (vite.config.ts, tailwind.config.js, postcss.config.js)
  // исполняются в Node — браузерные глобалы там ложные.
  {
    files: ['*.{ts,js}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.node,
    },
  },

  // Код приложения: браузер + React.
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // Ядро п. 31. Включаем точечно, а не весь react-hooks v7 recommended:
      // его компиляторные правила (purity, immutability, refs) рассчитаны
      // на React Compiler, которого в проекте нет.
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      // Fast Refresh теряет hot-reload при смешанных экспортах; это не баг,
      // поэтому предупреждение (как в шаблоне Vite).
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // П. 31 — линтер не должен валиться на стиле: `any` в дженерик-
      // обёртках (ComponentType<any> в lazyRoute) идиоматичен, поэтому
      // предупреждением, а не ошибкой.
      '@typescript-eslint/no-explicit-any': 'warn',
    },
  },
)
