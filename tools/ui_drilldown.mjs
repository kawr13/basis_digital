/**
 * Браузерная проверка детализации (drill-down) дашборда.
 *
 * Проверяет: клик по столбцу графика открывает панель за день, повторный клик и
 * Esc закрывают, клавиатура (Tab/Enter) работает, клик по строке ёмкости открывает
 * панель склада с остатками грузов, а менеджер получает запрос только по своим
 * складам (права фильтрует бэкенд).
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> [BASE=http://127.0.0.1:5173] [PASS=...] \
 *         node tools/ui_drilldown.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://127.0.0.1:5173";
const PASS = process.env.PASS || "Top-Secret-2026";

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
const problems = [];
page.on("pageerror", (e) => problems.push(`JS: ${e.message}`));
page.on("console", (m) => {
  if (m.type() !== "error") return;
  const t = m.text();
  if (t.includes("Failed to load resource")) return; // 401 в auth-потоке — норма
  problems.push(`console: ${t}`);
});

const ok = (cond, msg) => {
  console.log(`${cond ? "✅" : "❌"} ${msg}`);
  if (!cond) problems.push(msg);
};

async function login(loginName, password) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("input[autocomplete=username]", loginName);
  await page.fill("input[autocomplete=current-password]", password);
  await page.click("button.btn-primary");
  await page.waitForSelector("section[data-widget]", { timeout: 10000 });
}

/** Первый столбец графика, за которым есть данные. */
async function dataBar() {
  const bars = await page.$$('section[data-widget="dynamics"] rect[data-bar]');
  for (const b of bars) {
    const label = (await b.getAttribute("aria-label")) || "";
    if (!/обработано 0[.,]0 т/.test(label)) return b;
  }
  return bars[0] ?? null;
}

// ---------------------------------------------------------------- admin -----
await login("admin", PASS);

const bars = await page.$$('section[data-widget="dynamics"] rect[data-bar]');
ok(bars.length > 0, `график кликабельный: зон клика ${bars.length}`);
const bar = await dataBar();
const day = bar && (await bar.getAttribute("data-bar"));
ok(!!day, `у зоны клика есть data-bar (${day})`);
ok(
  (await bar.getAttribute("role")) === "button" && (await bar.getAttribute("tabindex")) === "0",
  "зона клика доступна с клавиатуры (role=button, tabindex=0)",
);
ok(!!(await bar.$("title")), "у столбца сохранился <title>-тултип");

// клик открывает панель за день
const reqs = [];
page.on("request", (r) => r.url().includes("/api/reports/") && reqs.push(r.url()));
await bar.click();
await page.waitForSelector("[data-drilldown]", { timeout: 5000 });
await page.waitForSelector("[data-drilldown][data-drilldown-state='ready']", { timeout: 8000 });
let panel = await page.$("[data-drilldown]");
ok((await panel.getAttribute("data-drilldown-mode")) === "day", "режим панели — день");
ok((await panel.getAttribute("data-drilldown-day")) === day, `панель открыта за ${day}`);
ok((await panel.getAttribute("role")) === "dialog", "у панели роль dialog");
ok(await panel.$("[data-drilldown-total]") !== null, "в таблице есть строка «Итого»");
ok(
  (await panel.$$eval("tbody tr", (rs) => rs.length)) > 0,
  `разрез по складам: ${(await panel.$$eval("tbody tr", (rs) => rs.length))} стр.`,
);
ok(
  await page.evaluate(() => document.activeElement?.hasAttribute("data-drilldown") ?? false),
  "фокус при открытии ушёл в панель",
);
ok(
  (await panel.getAttribute("aria-labelledby")) === "drilldown-title",
  "заголовок панели связан с dialog (aria-labelledby)",
);
const overlap = await page.evaluate(() => {
  const h = document.querySelector("header")?.getBoundingClientRect();
  const p = document.querySelector("[data-drilldown]")?.getBoundingClientRect();
  return !h || !p ? null : { top: p.top >= h.bottom - 1, right: p.right <= innerWidth + 1 };
});
ok(overlap && overlap.top && overlap.right, "панель не перекрывает шапку и влезает в окно");

// панель лежит ПОВЕРХ виджетов: сетка ради неё не должна сжиматься
const widthWith = await page.evaluate(
  () => document.querySelector("section[data-widget]")?.getBoundingClientRect().width ?? 0,
);
await page.keyboard.press("Escape");
await page.waitForSelector("[data-drilldown]", { state: "detached", timeout: 5000 });
const widthWithout = await page.evaluate(
  () => document.querySelector("section[data-widget]")?.getBoundingClientRect().width ?? 0,
);
ok(
  Math.abs(widthWith - widthWithout) < 2,
  `сетка не сжимается под панель: ${widthWithout.toFixed(0)}px → ${widthWith.toFixed(0)}px`,
);

// панель накладная — значит закрывается и по клику мимо
await bar.click();
await page.waitForSelector("[data-drilldown]", { timeout: 5000 });
await page.mouse.click(8, 500);
await page.waitForTimeout(400);
ok((await page.$("[data-drilldown]")) === null, "клик мимо панели закрывает её");
await bar.click();
await page.waitForSelector("[data-drilldown]", { timeout: 5000 });
ok(reqs.some((u) => u.includes("/api/reports/processing?from=") && !u.includes("warehouses=")),
  "админ: отчёт за день запрошен без ограничения складами");

// повторный клик по тому же столбцу закрывает
await (await page.$(`rect[data-bar="${day}"]`)).click();
await page.waitForSelector("[data-drilldown]", { state: "detached", timeout: 5000 });
ok(true, "повторный клик по тому же столбцу закрыл панель");

// клавиатура: Enter открывает, Esc закрывает
await page.focus(`rect[data-bar="${day}"]`);
await page.keyboard.press("Enter");
await page.waitForSelector("[data-drilldown]", { timeout: 5000 });
ok(true, "Enter по столбцу открыл панель");
ok(
  (await page.$eval(`rect[data-bar="${day}"]`, (e) => e.getAttribute("aria-selected"))) === "true",
  "выбранный столбец помечен aria-selected=true",
);
await page.keyboard.press("Escape");
await page.waitForSelector("[data-drilldown]", { state: "detached", timeout: 5000 });
ok(true, "Esc закрыл панель");
ok(
  await page.evaluate((d) => document.activeElement?.getAttribute("data-bar") === d, day),
  "после закрытия фокус вернулся на столбец",
);

// клик по строке ёмкости — режим «склад»
const wid = await page.$eval('section[data-widget="capacity"] [data-warehouse]', (e) =>
  e.getAttribute("data-warehouse"),
);
await page.click(`[data-warehouse="${wid}"]`);
await page.waitForSelector("[data-drilldown][data-drilldown-mode='warehouse']", { timeout: 5000 });
await page.waitForSelector("[data-drilldown][data-drilldown-state='ready']", { timeout: 8000 });
panel = await page.$("[data-drilldown]");
ok((await panel.getAttribute("data-drilldown-warehouse")) === wid, `панель открыта по складу ${wid}`);
ok((await panel.$("[data-drilldown-cargo]") !== null) , "в панели есть блок «Чем загружен склад»");
ok(
  reqs.some((u) => u.includes("/api/reports/cargo?") && u.includes(`warehouses=${wid}`)),
  "остатки запрошены по выбранному складу",
);
ok(
  (await page.$eval(`[data-warehouse="${wid}"]`, (e) => e.getAttribute("data-selected"))) === "1",
  "выбранная строка ёмкости подсвечена (data-selected=1)",
);
await page.click("[data-drilldown-close]");
await page.waitForSelector("[data-drilldown]", { state: "detached", timeout: 5000 });
ok(true, "кнопка «Закрыть» работает");

// переключение выбора не должно показывать данные прошлого запроса
await page.click(`rect[data-bar="${day}"]`);
await page.waitForSelector("[data-drilldown][data-drilldown-mode='day']", { timeout: 5000 });
const days = await page.$$eval('section[data-widget="dynamics"] rect[data-bar]', (es) =>
  es.map((e) => e.getAttribute("data-bar")),
);
const otherDay = days.find((d) => d !== day);
if (otherDay) {
  await page.click(`rect[data-bar="${otherDay}"]`);
  await page.waitForSelector(`[data-drilldown][data-drilldown-day="${otherDay}"]`, { timeout: 5000 });
  ok(true, `выбор сменился на ${otherDay} без подмены данных`);
  await page.keyboard.press("Escape");
  await page.waitForSelector("[data-drilldown]", { state: "detached", timeout: 5000 });
}

// --------------------------------------------------------------- manager ----
await page.click("button:has-text('Выйти')");
await page.waitForSelector("input[autocomplete=username]", { timeout: 5000 });
await login("petrov", "demo1234");
const mreqs = [];
page.on("request", (r) => r.url().includes("/api/reports/processing") && mreqs.push(r.url()));
const mbar = await dataBar();
await mbar.click();
await page.waitForSelector("[data-drilldown][data-drilldown-state='ready']", { timeout: 8000 });
const scopeNote = await page.$eval("[data-drilldown]", (e) => e.textContent);
ok(/склада\(ов\) — ваши доступные/.test(scopeNote), "менеджер: в панели указано, что склады его");
ok(mreqs.some((u) => /warehouses=\d+(,\d+)+/.test(u)), "менеджер: отчёт запрошен только по своим складам");
const rows = await page.$$eval("[data-drilldown] tbody tr", (rs) => rs.length);
ok(rows > 0 && rows <= 2, `менеджер: в разрезе только его склады (${rows} стр.)`);
await page.keyboard.press("Escape");
await page.waitForSelector("[data-drilldown]", { state: "detached", timeout: 5000 });

// мобильный вид: панель снизу на всю ширину, шапку не перекрывает
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(300);
const mbar2 = await dataBar();
await mbar2.click();
await page.waitForSelector("[data-drilldown]", { timeout: 5000 });
const mob = await page.evaluate(() => {
  const h = document.querySelector("header").getBoundingClientRect();
  const p = document.querySelector("[data-drilldown]").getBoundingClientRect();
  return { left: p.left, right: p.right, top: p.top, headerBottom: h.bottom };
});
ok(
  mob.left <= 1 && mob.right >= 389 && mob.top >= mob.headerBottom,
  `мобильный: панель снизу на всю ширину (${Math.round(mob.left)}…${Math.round(mob.right)}, top ${Math.round(mob.top)} > шапки ${Math.round(mob.headerBottom)})`,
);

await browser.close();
console.log(problems.length ? `\nПРОБЛЕМЫ:\n- ${problems.join("\n- ")}` : "\nвсе проверки прошли");
process.exit(problems.length ? 1 : 0);
