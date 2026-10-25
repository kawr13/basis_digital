/**
 * Замер производительности первой отрисовки «Статистики» (TASKS.md, п.8 и п.15).
 *
 * Мёрит ТОЛЬКО прод-сборку: в dev vite отдаёт несжатые модули и работает React с
 * strict-mode-дублями, поэтому цифры «до/после» на dev-сервере бессмысленны.
 *
 * Что именно мёрится:
 *   1) вход → первая отрисовка «Статистики» (34 плашки рисуются после ответа
 *      /api/dispatcher/overview) — сумма длинных задач и топ-5 по времени;
 *   2) смена пресета периода (клик «30 сут» после «14 сут») — сумма длинных
 *      задач ПОСЛЕ клика, до того как страница снова замолчала.
 * Плюс FCP, DOMContentLoaded, вес ресурсов по типам и число узлов DOM.
 *
 * Границы фазы считает не по таймерам вслепую, а по «тишине»: фаза закончена,
 * когда с последнего длинного задача прошло QUIET_MS и плашки на экране. Иначе
 * прогон либо резал бы реальную работу, либо тащил в сумму случайные задачи
 * 15-секундного опроса свежести.
 *
 * Запуск (прод-сборка + preview c прокси /api на :8000):
 *   cd frontend && npm run build
 *   npx vite preview --port 4199 --host 127.0.0.1
 *   PW_CHROMIUM=<путь к chrome.exe> BASE=http://127.0.0.1:4199 PASS=... node tools/ui_perf.mjs
 *
 * Пороги: [LT_PAINT_MS=150] (первая отрисовка) и [LT_PERIOD_MS=120] (смена
 * периода). Просрочка — ❌ и exit 1.
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://127.0.0.1:4199";
const PASS = process.env.PASS || "Top-Secret-2026";
const LOGIN = process.env.LOGIN || "admin";
const LIMIT_PAINT = Number(process.env.LT_PAINT_MS || 150);
const LIMIT_PERIOD = Number(process.env.LT_PERIOD_MS || 120);
/** Пауза без длинных задач, после которой считаем фазу законченной. */
const QUIET_MS = Number(process.env.QUIET_MS || 800);
const TIMEOUT_MS = Number(process.env.PHASE_TIMEOUT_MS || 30_000);

const problems = [];
const ok = (cond, msg) => {
  console.log(`${cond ? "✅" : "❌"} ${msg}`);
  if (!cond) problems.push(msg);
};
const kb = (bytes) => `${(bytes / 1024).toFixed(1)} КБ`;
const ms = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)} мс`);

/**
 * PerformanceObserver вешается ДО загрузки страницы: addInitScript выполняется
 * до скриптов документа, а длинные задачи наблюдаются только с момента
 * подписки — подписавшись после goto, половину работы и не увидишь.
 */
const INIT_SCRIPT = () => {
  window.__lt = [];
  window.__ltError = null;
  window.__t0 = 0;
  window.__t1 = 0;
  try {
    const po = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        const a = e.attribution && e.attribution[0];
        window.__lt.push({
          s: e.startTime,
          d: e.duration,
          attr: a ? `${a.name}${a.containerType && a.containerType !== "window" ? `:${a.containerType}` : ""}` : "?",
        });
      }
    });
    po.observe({ entryTypes: ["longtask"] });
  } catch (e) {
    window.__ltError = String(e);
  }
};

/**
 * Ждём окончания фазы: плашки на месте И длинных задач нет QUIET_MS подряд.
 * Возвращает собранные за фазу задачи (чтобы сумма считалась по факту, а не по
 * «сколько успели накопиться к моменту возврата»).
 */
async function waitPhase(page, { since, minWidgets, quietMs = QUIET_MS, timeout = TIMEOUT_MS } = {}) {
  return page.evaluate(
    async ([since, minWidgets, quietMs, timeout]) => {
      const start = performance.now();
      for (;;) {
        await new Promise((r) => setTimeout(r, 100));
        const tasks = window.__lt.filter((e) => e.s >= since);
        const lastEnd = tasks.length ? Math.max(...tasks.map((e) => e.s + e.d)) : since;
        const widgets = document.querySelectorAll("section[data-widget]").length;
        const now = performance.now();
        const painted = widgets >= minWidgets;
        const quiet = tasks.length ? now - lastEnd >= quietMs : now - since >= quietMs;
        if ((painted && quiet) || now - start > timeout) {
          return { tasks, widgets, end: Math.max(now, lastEnd), timeout: now - start > timeout };
        }
      }
    },
    [since, minWidgets, quietMs, timeout],
  );
}

const sum = (tasks) => Math.round(tasks.reduce((a, e) => a + e.d, 0));
const top = (tasks, n = 5) =>
  [...tasks]
    .sort((a, b) => b.d - a.d)
    .slice(0, n)
    .map((e) => `${Math.round(e.d)} мс @${Math.round(e.s)} мс [${e.attr}]`)
    .join(", ") || "нет длинных задач";

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });

/** Вход + подготовка: режим страницы должен быть «подробно» (34 блока), иначе
 *  мёрим 30 блоков и сравниваем «до/после» с самим не тем. Режим — настройка на
 *  человеке, поэтому готовим её отдельным заходом, а чистый замер идёт уже в
 *  новом контексте (без cookie = реальный экран входа). */
async function open() {
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  await page.addInitScript(INIT_SCRIPT);
  page.on("pageerror", (e) => problems.push(`JS: ${e.message}`));
  await page.goto(`${BASE}/login`, { waitUntil: "load" });
  await page.waitForSelector("input[autocomplete=username]", { timeout: 15000 });
  await page.fill("input[autocomplete=username]", LOGIN);
  await page.fill("input[autocomplete=current-password]", PASS);
  return { ctx, page };
}

// --- подготовительный заход: фиксируем режим и греем кэш агрегатов бэкенда ---
{
  const { ctx, page } = await open();
  await page.click("button.btn-primary");
  await page.waitForSelector("[data-stats-toolbar]", { timeout: 20000 });
  await page.waitForSelector("section[data-widget] svg", { timeout: 20000 });
  const active = await page.$eval("[data-stats-mode] button.bg-ink-700", (e) => e.textContent || "").catch(() => "");
  if (!/Подробно/.test(active)) {
    await page.click('[data-stats-mode] >> text=Подробно');
    await page.waitForTimeout(1500);
    console.log("ℹ️  режим «Статистики» переключён в «подробно» (34 блока) — он персональная настройка");
  }
  await ctx.close();
}

// --- чистый замер -----------------------------------------------------------
const { ctx, page } = await open();

if (await page.evaluate(() => !!window.__ltError)) {
  console.log(`❌ PerformanceObserver(longtask) недоступен: ${await page.evaluate(() => window.__ltError)}`);
  await browser.close();
  process.exit(1);
}

// «вход» — нажатие кнопки; всё, что было до него (загрузка экрана входа),
// в сумму первой отрисовки не входит
await page.evaluate(() => {
  window.__t0 = performance.now();
});
await page.click("button.btn-primary");
await page.waitForSelector("section[data-widget] svg", { timeout: 20000 });
const firstChartMs = await page.evaluate(() => performance.now() - window.__t0);

const paint = await waitPhase(page, { since: await page.evaluate(() => window.__t0), minWidgets: 20 });
const paintSum = sum(paint.tasks);

// --- смена пресета периода --------------------------------------------------
// Пресет «30 сут» мёрим ВСЕГДА после «14 сут»: если период уже 30 суток, клик
// по «30 сут» дал бы клик по уже активной кнопке и цифра была бы льстивой.
await page.click("text=14 сут");
await waitPhase(page, { since: await page.evaluate(() => performance.now() - 100), minWidgets: 20, quietMs: 600 });

await page.evaluate(() => {
  window.__t1 = performance.now();
});
/** «данные за 14.09.2026 — 13.10.2026» — единственная часть панели периода,
 * которая меняется от пресета: хвост подсказки («в витрине с …», «выгружено …»)
 * одинаковый, сравнивать его — проверка ни о чём. */
const shownPeriod = () =>
  page.$eval("[data-stats-toolbar]", (e) => (/данные за [^·]+/.exec(e.textContent || "") || ["—"])[0].replace(/\s+/g, " ").trim());
const periodTextBefore = await shownPeriod();
await page.click("text=30 сут");
const period = await waitPhase(page, { since: await page.evaluate(() => window.__t1), minWidgets: 20, quietMs: 600 });
const periodSum = sum(period.tasks);
const periodTextAfter = await shownPeriod();

// --- метрики документа ------------------------------------------------------
const stats = await page.evaluate(() => {
  const nav = performance.getEntriesByType("navigation")[0];
  const fcp = performance.getEntriesByType("paint").find((p) => p.name === "first-contentful-paint");
  const res = performance.getEntriesByType("resource");
  const byType = {};
  let transfer = 0;
  let decoded = 0;
  for (const r of res) {
    const t = r.initiatorType || "other";
    const b = (byType[t] = byType[t] || { n: 0, transfer: 0, decoded: 0 });
    b.n += 1;
    b.transfer += r.transferSize || 0;
    b.decoded += r.decodedBodySize || 0;
    transfer += r.transferSize || 0;
    decoded += r.decodedBodySize || 0;
  }
  return {
    fcp: fcp ? fcp.startTime : null,
    dcl: nav ? nav.domContentLoadedEventEnd : null,
    load: nav ? nav.loadEventEnd : null,
    byType,
    res: { n: res.length, transfer, decoded },
    nodes: document.getElementsByTagName("*").length,
    widgets: document.querySelectorAll("section[data-widget]").length,
    svgs: document.querySelectorAll("section[data-widget] svg").length,
  };
});

await ctx.close();
await browser.close();

// --- сводка -----------------------------------------------------------------
const periodChanged = periodTextBefore.slice(-60) !== periodTextAfter.slice(-60);
console.log("\n=== Замер «Статистики» (прод-сборка) ===");
console.log(`FCP                          : ${ms(stats.fcp)}`);
console.log(`DOMContentLoaded             : ${ms(stats.dcl)}`);
console.log(`загрузка документа           : ${ms(stats.load)}`);
console.log(`вход → первый график         : ${ms(firstChartMs)}`);
console.log(
  `ресурсы                      : ${stats.res.n} шт · ${kb(stats.res.transfer)} передано · ${kb(stats.res.decoded)} в теле`,
);
for (const [t, b] of Object.entries(stats.byType).sort((a, z) => z[1].transfer - a[1].transfer)) {
  console.log(`  ${t.padEnd(11)} ${String(b.n).padStart(3)} шт · ${kb(b.transfer).padStart(9)} · ${kb(b.decoded).padStart(9)}`);
}
console.log(`узлов DOM                    : ${stats.nodes}`);
console.log(`плашек/графиков              : ${stats.widgets} / ${stats.svgs}`);
console.log(`\nдлинные задачи, первая отрисовка: ${paintSum} мс из ${paint.tasks.length} задач (порог ${LIMIT_PAINT} мс)`);
console.log(`  топ-5: ${top(paint.tasks)}`);
console.log(`длинные задачи, смена периода : ${periodSum} мс из ${period.tasks.length} задач (порог ${LIMIT_PERIOD} мс)`);
console.log(`  топ-5: ${top(period.tasks)}`);

ok(paintSum <= LIMIT_PAINT, `первая отрисовка: сумма длинных задач ${paintSum} мс ≤ ${LIMIT_PAINT} мс`);
ok(periodSum <= LIMIT_PERIOD, `смена периода: сумма длинных задач ${periodSum} мс ≤ ${LIMIT_PERIOD} мс`);
ok(periodChanged, `пресет периода реально сменил период: ${periodTextBefore} → ${periodTextAfter}`);
ok(stats.widgets >= 30, `рисуется полный набор блоков: ${stats.widgets} плашек, ${stats.svgs} SVG`);
ok(!paint.timeout && !period.timeout, "фазы отрисовки закончились до таймаута");

console.log(
  problems.length
    ? `\nПРОБЛЕМЫ (${problems.length}):\n- ` + problems.join("\n- ")
    : "\nВсе проверки прошли",
);
process.exit(problems.length ? 1 : 0);
