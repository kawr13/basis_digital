/**
 * Браузерная проверка страницы «Статистика» (отчёт диспетчера).
 *
 * Проверяет, что страница собрана из реальных данных витрины: период по
 * умолчанию берётся от последней даты данных, графики не пустые, клик по
 * суткам открывает панель смены, фильтр по смене меняет цифры.
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> [BASE=http://127.0.0.1:5173] [PASS=...] \
 *         node tools/ui_statistics.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://127.0.0.1:5173";
const PASS = process.env.PASS || "Top-Secret-2026";
const LOGIN = process.env.LOGIN || "admin";
const SHOTS = process.env.SHOTS !== "0";

const problems = [];
const ok = (cond, msg) => {
  console.log(`${cond ? "✅" : "❌"} ${msg}`);
  if (!cond) problems.push(msg);
};

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
page.on("pageerror", (e) => problems.push(`JS: ${e.message}`));
// 401 на /api/auth/me и /api/auth/refresh — штатная проверка сессии до входа,
// это не ошибка страницы; всё остальное (500, упавшие запросы данных) — ошибка.
const AUTH_BOOTSTRAP = /\/api\/auth\/(me|refresh)$/;
page.on("response", (r) => {
  if (r.status() >= 400 && !AUTH_BOOTSTRAP.test(new URL(r.url()).pathname)) {
    problems.push(`HTTP ${r.status()} ${r.url()}`);
  }
});

// ---- вход ------------------------------------------------------------------
await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill("input[autocomplete=username]", LOGIN);
await page.fill("input[autocomplete=current-password]", PASS);
await page.click("button.btn-primary");
await page.waitForSelector("[data-stats-toolbar]", { timeout: 15000 });
await page.waitForSelector("section[data-widget]", { timeout: 15000 });
await page.waitForTimeout(900);

const url = page.url();
ok(/\/$/.test(new URL(url).pathname), `«Статистика» — главная страница (${url})`);
ok(!!(await page.$('nav >> text=Статистика')), "вкладка «Статистика» в шапке");
ok(!!(await page.$('nav >> text=Склады 1С')), "витрина складов 1С уехала на свою вкладку");

// ---- период по умолчанию ---------------------------------------------------
const periodText = await page.$eval("[data-stats-toolbar]", (e) => e.textContent.replace(/\s+/g, " "));
ok(/данные за \d{2}\.\d{2}\.\d{4} — \d{2}\.\d{2}\.\d{4}/.test(periodText), `период показан: ${periodText.slice(-90)}`);
const toValue = await page.$eval('input[type=date]:nth-of-type(2)', (e) => e.value).catch(() => "");
ok(!!toValue, `дата «по» выбрана: ${toValue}`);

// ---- виджеты ---------------------------------------------------------------
// режим страницы наследуется между прогонами (он в настройках пользователя),
// поэтому перед замерами фиксируем «подробно»
await page.click('[data-stats-mode] >> text=Подробно');
await page.waitForTimeout(1200);
const widgets = await page.$$eval("section[data-widget]", (els) => els.map((e) => e.dataset.widget));
ok(widgets.length >= 20, `виджетов на странице: ${widgets.length}`);
const required = [
  "kpi_throughput", "kpi_plan", "kpi_calls", "kpi_tph", "kpi_downtime", "kpi_rail",
  "kpi_trucks", "kpi_crew", "kpi_rs", "dynamics", "cargo_mix", "cumulative", "pareto",
  "plan_fact", "berths", "vessels_top", "vessels_scatter", "shift_compare", "weekday_heat",
  "personnel", "absence", "equipment", "rail_day", "rail_cargo", "trucks_day",
  "trucks_cargo", "direction", "gauges", "arrivals", "rail_arrivals", "vessels_table",
];
const missing = required.filter((id) => !widgets.includes(id));
ok(missing.length === 0, `все задуманные блоки на месте${missing.length ? `; нет: ${missing.join(", ")}` : ""}`);

// графики должны быть нарисованы, а не «нет данных»
const svgs = await page.$$eval("section[data-widget] svg", (els) => els.length);
ok(svgs >= 14, `SVG-графиков нарисовано: ${svgs}`);
const empties = await page.$$eval("section[data-widget]", (els) =>
  els.filter((e) => /Нет данных за период/.test(e.textContent || "")).map((e) => e.dataset.widget),
);
ok(empties.length === 0, `пустых графиков нет${empties.length ? `: ${empties.join(", ")}` : ""}`);

// цифры KPI не нулевые
const kpiValue = async (id) =>
  String(await page.$eval(`section[data-widget="${id}"] .num`, (e) => (e.textContent || "").trim()));
const throughput = await kpiValue("kpi_throughput");
ok(throughput !== "0" && throughput !== "", `грузопоток посчитан: ${throughput} т`);
const plan = await kpiValue("kpi_plan");
ok(/%$/.test(plan), `выполнение плана: ${plan}`);

// ---- клик по суткам → панель смены ----------------------------------------
const bars = await page.$$('section[data-widget="dynamics"] rect[data-bar]');
ok(bars.length > 0, `график грузопотока кликабельный: зон ${bars.length}`);
let picked = null;
for (const b of bars) {
  const label = (await b.getAttribute("aria-label")) || "";
  if (!/факт 0[.,]0/.test(label)) { picked = b; break; }
}
const day = picked && (await picked.getAttribute("data-bar"));
ok(!!day, `у колонки есть data-bar (${day})`);
await picked.click();
await page.waitForSelector("[data-drilldown='dispatcher-day']", { timeout: 8000 });
const panelDay = await page.getAttribute("[data-drilldown='dispatcher-day']", "data-drilldown-day");
ok(panelDay === day, `панель смены открылась на ${panelDay}`);
const panelText = await page.$eval("[data-drilldown='dispatcher-day']", (e) => e.textContent || "");
ok(/ричстакеры/i.test(panelText), "в панели есть смена с ричстакерами");
ok(/Суда · [1-9]\d*/.test(panelText), "в панели есть строки обработки судов");
if (SHOTS) await page.screenshot({ path: "shot_stats_day.png" });

await page.keyboard.press("Escape");
await page.waitForTimeout(300);
ok((await page.$("[data-drilldown='dispatcher-day']")) === null, "Esc закрывает панель смены");

// ---- фильтр по смене -------------------------------------------------------
const before = await kpiValue("kpi_throughput");
await page.click("text=День 08–20");
await page.waitForTimeout(900);
const after = await kpiValue("kpi_throughput");
ok(before !== after, `фильтр по смене меняет цифры: ${before} → ${after}`);
await page.click("text=Все смены");
await page.waitForTimeout(700);

// ---- пресет периода --------------------------------------------------------
await page.click("text=7 сут");
await page.waitForTimeout(900);
const period7 = await page.$eval("[data-stats-toolbar]", (e) => e.textContent.replace(/\s+/g, " "));
ok(/данные за \d{2}\.\d{2}\.\d{4} — \d{2}\.\d{2}\.\d{4}/.test(period7), `пресет «7 сут» применил период: ${period7.slice(-90)}`);
await page.click("text=30 сут");
await page.waitForTimeout(800);

// ---- раскладка -------------------------------------------------------------
await page.click("text=Настроить");
await page.waitForSelector("[data-handle]");
const order = async () => (await page.$$eval("section[data-widget]", (els) => els.map((e) => e.dataset.widget))).join();
const reset = async () => {
  await page.click("text=Сбросить");
  await page.waitForTimeout(500);
};
await reset();                       // стартуем с типовой раскладки, иначе повторный запуск не что менять
const before2 = await order();
const ids = before2.split(",");
// тащим четвёртый блок на второй — порядок обязан измениться
await page.dragAndDrop(`[data-handle="${ids[3]}"]`, `section[data-widget="${ids[1]}"]`);
await page.waitForTimeout(500);
const after2 = await order();
ok(before2 !== after2, `блоки переставляются перетаскиванием (${ids[3]} → ${ids[1]})`);
await reset();                       // не оставляем после себя мусорную раскладку
await page.click("text=Готово");
await page.waitForTimeout(600);

// ---- выводка/постановка вагонов и платформы из аренды -----------------------
const newBlocks = ["rail_moves", "rail_wagon_mix", "platforms"];
const missingNew = newBlocks.filter((id) => !widgets.includes(id));
ok(missingNew.length === 0, `новые блоки витрины на месте${missingNew.length ? `; нет: ${missingNew.join(", ")}` : ""}`);
const movesHint = await page.$eval('section[data-widget="rail_moves"]', (e) => e.textContent || "");
ok(/выведено [\d  ]+ · поставлено [\d  ]+ ваг\./.test(movesHint.replace(/\s+/g, " ")),
   `выводка/постановка посчитаны: ${(movesHint.match(/выведено[^·]*·[^·]*ваг\./) || ["—"])[0]}`);

// ---- клик по графику = таблица строк ---------------------------------------
const berthBar = await page.$('section[data-widget="berths"] [data-bar]');
ok(!!berthBar, "полоса причала кликабельна");
const berthName = berthBar && (await berthBar.getAttribute("data-bar"));
await berthBar.click();
await page.waitForSelector("[data-drilldown='dispatcher-rows']", { timeout: 8000 });
const rowsKind = await page.getAttribute("[data-drilldown='dispatcher-rows']", "data-drilldown-kind");
ok(rowsKind === "berth", `открылись строки витрины по причалу (${rowsKind})`);
// панель появляется сразу (с надписью «загружаем»), строки приходят ответом —
// ждём именно tbody, иначе проверка читает пустой экран и враньёт
await page.waitForSelector("[data-drilldown='dispatcher-rows'] tbody tr", { timeout: 10000 });
const rowsText = await page.$eval("[data-drilldown='dispatcher-rows']", (e) => e.textContent || "");
ok(/строк: [1-9]\d*/.test(rowsText.replace(/\s+/g, " ")), "в панели есть число строк");
const rowCells = await page.$$eval("[data-drilldown='dispatcher-rows'] tbody tr", (els) => els.length);
ok(rowCells > 0, `таблица заполнена: ${rowCells} строк`);
ok(await page.$eval("[data-rows-csv]", (e) => !e.disabled), "из панели можно выгрузить CSV");
await page.click("[data-rows-close]");
await page.waitForTimeout(300);
ok((await page.$("[data-drilldown='dispatcher-rows']")) === null, "панель строк закрывается");

// «пистон» план/факт: подпись «17 нояб.» ушла бы в /day вместо даты (422)
const bullet = await page.$('section[data-widget="plan_fact"] [data-bar]');
ok(!!bullet, "строка план/факт кликабельна");
const bulletKey = await bullet.getAttribute("data-bar");
ok(/^\d{4}-\d{2}-\d{2}$/.test(bulletKey || ""), `план/факт отдаёт дату, а не подпись: ${bulletKey}`);
await bullet.click();
await page.waitForSelector("[data-drilldown='dispatcher-day']", { timeout: 8000 });
ok((await page.$eval("[data-drilldown='dispatcher-day']", (e) => e.textContent || "")).includes("Смена"),
   "план/факт открывает панель смены");
await page.keyboard.press("Escape");
await page.waitForTimeout(300);

// станции: полоса «Ж/Д на подходе» вела на 500 (в разрезе не было части колонок)
const stBar = await page.$('section[data-widget="rail_arrivals"] [data-bar]');
ok(!!stBar, "станция кликабельна");
await stBar.click();
await page.waitForSelector("[data-drilldown='dispatcher-rows'] tbody tr", { timeout: 10000 });
const stRows = await page.$$eval("[data-drilldown='dispatcher-rows'] tbody tr", (els) => els.length);
ok(stRows > 0, `строки по станции приехали: ${stRows}`);
await page.keyboard.press("Escape");
await page.waitForTimeout(300);

// клик по составу вагонов (без ключа) — список выводки/постановки
const mixBar = await page.$('section[data-widget="rail_wagon_mix"] [data-bar]');
ok(!!mixBar, "типы вагонов кликабельны");
await mixBar.click();
await page.waitForSelector("[data-drilldown='dispatcher-rows'] tbody tr", { timeout: 10000 });
const moveRows = await page.$eval("[data-drilldown='dispatcher-rows'] tbody tr", (e) => e.textContent || "");
ok(/вывод|ввод/.test(moveRows), `в списке есть выводка/постановка: ${moveRows.slice(0, 40)}`);
await page.keyboard.press("Escape");
await page.waitForTimeout(300);

// ---- режим «консервативно»: один визуальный язык ----------------------------
await page.click('[data-stats-mode] >> text=Консервативно');
await page.waitForTimeout(1200);
const liteWidgets = await page.$$eval("section[data-widget]", (els) => els.map((e) => e.dataset.widget));
ok(liteWidgets.length < widgets.length, `консервативный набор короче: ${widgets.length} → ${liteWidgets.length}`);
ok(liteWidgets.length >= 20, `консервативный режим не обеднел до бессмыслицы: ${liteWidgets.length} блоков`);
const banned = ["cargo_mix", "cumulative", "pareto", "vessels_scatter", "shift_compare", "weekday_heat", "direction", "gauges"];
const stillThere = banned.filter((id) => liteWidgets.includes(id));
ok(stillThere.length === 0, `колец, радаров, пузырьков и тепловых карт нет${stillThere.length ? `; остались: ${stillThere.join(", ")}` : ""}`);
const liteEmpty = await page.$$eval("section[data-widget]", (els) =>
  els.filter((e) => /Нет данных за период/.test(e.textContent || "")).map((e) => e.dataset.widget),
);
ok(liteEmpty.length === 0, `в консервативном режиме пустых графиков нет${liteEmpty.length ? `: ${liteEmpty.join(", ")}` : ""}`);
for (const id of ["cargo_bars", "shift_bars", "weekday_bars", "direction_bars", "rail_moves", "platforms"]) {
  ok(liteWidgets.includes(id), `блок «${id}» на месте`);
}

// режим и раскладка переживают перезагрузку
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector("[data-stats-toolbar]", { timeout: 15000 });
await page.waitForTimeout(1200);
const afterReload = await page.$$eval("section[data-widget]", (els) => els.map((e) => e.dataset.widget));
ok(afterReload.length === liteWidgets.length, `режим запомнился после перезагрузки (${afterReload.length} блоков)`);
const activeMode = await page.$eval("[data-stats-mode] button.bg-ink-700", (e) => e.textContent || "");
ok(/Консервативно/.test(activeMode), `активный режим подписан: ${activeMode.trim()}`);
await page.click('[data-stats-mode] >> text=Подробно');
await page.waitForTimeout(1000);
const backToFull = await page.$$eval("section[data-widget]", (els) => els.map((e) => e.dataset.widget));
ok(backToFull.length === widgets.length, `возврат к подробному набору: ${backToFull.length} блоков`);

// ---- импорт файла в админке -------------------------------------------------
await page.click("nav >> text=Администрирование");
await page.waitForSelector('[data-tab="onec"]', { timeout: 10000 });
await page.click('[data-tab="onec"]');
await page.waitForSelector("[data-dispatcher-panel]", { timeout: 10000 });
const adminText = await page.$eval("[data-dispatcher-panel]", (e) => e.textContent || "");ok(/файл найден/.test(adminText), "админка видит файл отчёта диспетчера");
ok(/автозагрузка вкл/.test(adminText), "автозагрузка при старте включена");
const pathValue = await page.$eval("[data-dispatcher-path]", (e) => e.value);
ok(/\.xlsm$/i.test(pathValue), `путь подставлен из настроек: ${pathValue}`);
await page.click('[data-admin-action="dispatcher-import"]');
await page.waitForSelector("[data-dispatcher-result]", { timeout: 60000 });
const rows = await page.$$eval("[data-dispatcher-result] tbody tr", (els) =>
  els.map((e) => `${e.children[0].textContent}=${e.children[1].textContent}`),
);
ok(rows.length >= 4, `импорт по пути отработал: ${rows.join(", ")}`);

// ---- скриншоты -------------------------------------------------------------
if (SHOTS) {
  await page.click("nav >> text=Статистика");
  await page.waitForSelector("[data-stats-toolbar]", { timeout: 10000 });
  await page.waitForTimeout(900);
  await page.screenshot({ path: "shot_stats_top.png" });
  await page.screenshot({ path: "shot_stats_full.png", fullPage: true });
}

await browser.close();
console.log(problems.length ? `\nПРОБЛЕМЫ (${problems.length}):\n- ` + problems.join("\n- ") : "\nВсе проверки прошли");
process.exit(problems.length ? 1 : 0);
