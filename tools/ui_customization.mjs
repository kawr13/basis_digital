/**
 * Браузерная проверка новых фич: перетаскивание плашек, ширина, скрытие,
 * сохранение раскладки после перезагрузки, вкладка «Отчёты» и выгрузка CSV.
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> node tools/ui_customization.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://127.0.0.1:5173";
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
const problems = [];
page.on("pageerror", (e) => problems.push(`JS: ${e.message}`));

const order = () => page.$$eval("section[data-widget]", (els) => els.map((e) => e.dataset.widget));
const ok = (cond, msg) => console.log(`${cond ? "✅" : "❌"} ${msg}`) || (cond ? 0 : problems.push(msg));

// Раскладка и фильтры хранятся на сервере, поэтому прогон делаем герметичным:
// чистим сохранённую раскладку администратора, иначе проверки «сбросил к 8/12 и 240 px»
// и «порядок пережил перезагрузку» зависят от того, что осталось от прошлого прогона.
const API = process.env.API || "http://127.0.0.1:8000";
try {
  const tok = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ login: "admin", password: process.env.PASS || "Top-Secret-2026" }),
  })
    .then((r) => r.json())
    .then((d) => d.access_token);
  const del = await fetch(`${API}/api/settings/preferences/dashboard_layout`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${tok}` },
  });
  console.log(`сброс сохранённой раскладки перед прогоном: HTTP ${del.status}`);
} catch (e) {
  console.log("не удалось сбросить раскладку (прогон может быть зависим от прошлого):", e.message);
}

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill("input[autocomplete=username]", "admin");
await page.fill("input[autocomplete=current-password]", process.env.PASS || "Top-Secret-2026");
await page.click("button.btn-primary");
await page.waitForSelector("section[data-widget]", { timeout: 10000 });

// «Статистика» стала главной страницей, витрина складов 1С — на вкладке «Склады 1С».
await page.goto(`${BASE}/warehouses`, { waitUntil: "networkidle" });
await page.waitForSelector("section[data-widget]", { timeout: 10000 });

const before = await order();
console.log("порядок до:", before.join(", "));

// ---- режим настройки + перетаскивание -------------------------------------
await page.click("text=Настроить");
await page.waitForSelector("[data-handle]");
await page.dragAndDrop('[data-handle="kpi_load"]', 'section[data-widget="kpi_cargo"]');
const afterDrag = await order();
console.log("порядок после drag:", afterDrag.join(", "));
ok(afterDrag.join() !== before.join(), "плашка переставилась перетаскиванием");

// ---- ширина ---------------------------------------------------------------
await page.click('section[data-widget="kpi_cargo"] [title="ширина во всю"]');
const span = await page.$eval('section[data-widget="kpi_cargo"]', (e) => e.style.gridColumn);
ok(span === "span 12", `ширина сменилась (${span})`);

// ---- скрыть / вернуть -----------------------------------------------------
await page.click('section[data-widget="capacity"] button[title="убрать с дашборда"]');
await page.waitForTimeout(200);
ok((await order()).indexOf("capacity") === -1, "плашку можно скрыть");
await page.click("text=+ Ёмкость складов");
await page.waitForTimeout(200);
ok((await order()).includes("capacity"), "скрытую плашку можно вернуть");

// ---- размеры: высота, ширина, оба сразу ---------------------------------
const bodyH = (id) => page.$eval(`section[data-widget="${id}"] .widget-body`, (e) => e.offsetHeight);
const spanOf = (id) => page.$eval(`section[data-widget="${id}"]`, (e) => Number(e.dataset.span));

async function dragEdge(id, which, dx, dy) {
  const box = await page.locator(`section[data-widget="${id}"] [data-resize="${which}"]`).boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + dx, box.y + box.height / 2 + dy, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(200);
}

const h0 = await bodyH("dynamics");
await dragEdge("dynamics", "h", 0, 140);
const h1 = await bodyH("dynamics");
ok(h1 > h0 + 100, `высота выросла тягой за нижний край: ${h0} → ${h1} px`);

const s0 = await spanOf("dynamics");
await dragEdge("dynamics", "w", -300, 0);
const s1 = await spanOf("dynamics");
ok(s1 < s0, `ширина уменьшилась тягой за правый край: ${s0} → ${s1} из 12`);

await dragEdge("capacity", "both", 220, 90);
const capSpan = await spanOf("capacity");
const capH = await bodyH("capacity");
ok(capSpan > 4 && capH > 300, `угол меняет ширину и высоту сразу: ${capSpan}/12, ${capH} px`);

await page.dblclick('section[data-widget="dynamics"] [data-handle="dynamics"]');
await page.waitForTimeout(250);
const resetSpan = await spanOf("dynamics");
const resetH = await bodyH("dynamics");
ok(resetSpan === 8 && resetH === 240, `двойной клик по заголовку вернул размер (${resetSpan}/12, ${resetH} px)`);

await page.screenshot({ path: "shot_edit_mode.png" });
await page.click("text=Готово");
await page.waitForTimeout(800); // дебаунс сохранения на сервер

// ---- раскладка переживает перезагрузку страницы ---------------------------
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector("section[data-widget]", { timeout: 10000 });
const afterReload = await order();
console.log("порядок после перезагрузки:", afterReload.join(", "));
ok(afterReload.join() === afterDrag.join(), "раскладка сохранилась на сервере и подтянулась после перезагрузки");
const spanReload = await page.$eval('section[data-widget="kpi_cargo"]', (e) => e.style.gridColumn);
ok(spanReload === "span 12", "ширина тоже запомнилась");
ok((await spanOf("capacity")) === capSpan && (await bodyH("capacity")) === capH,
  `размер плашки пережил перезагрузку (${capSpan}/12, ${capH} px)`);

// ---- отчёты ---------------------------------------------------------------
await page.click("nav >> text=Отчёты");
await page.waitForSelector("table tfoot tr", { timeout: 10000 }); // ждём строку «Итого» = данные пришли
const rowsAll = await page.$$eval("table tbody tr", (r) => r.length);
ok(rowsAll > 0, `отчёт «Обработка по дням»: ${rowsAll} строк`);

await page.click("text=7 дней");
await page.waitForTimeout(700);
const rows7 = await page.$$eval("table tbody tr", (r) => r.length);
ok(rows7 < rowsAll, `период 7 дней короче 30: ${rows7} < ${rowsAll}`);

const chip = await page.$$("button.chip");
if (chip.length) {
  await chip[0].click();
  await page.waitForTimeout(700);
  const rowsOne = await page.$$eval("table tbody tr", (r) => r.length);
  ok(rowsOne <= rows7, `фильтр по складу работает: ${rowsOne} строк`);
  await chip[0].click();
}

// сортировка по колонке
await page.click("th >> text=Обработано, т");
await page.waitForTimeout(300);
const sortMark = await page.$eval("th >> text=Обработано, т", (e) => e.textContent);
ok(/[↑↓]/.test(sortMark), `сортировка по колонке (${sortMark.trim()})`);

// выгрузка CSV
const [download] = await Promise.all([
  page.waitForEvent("download", { timeout: 10000 }),
  page.click("text=Выгрузить CSV"),
]);
const suggested = download.suggestedFilename();
const path = await download.path();
const fs = await import("node:fs");
const rawCsv = fs.readFileSync(path, "utf8");
const head = rawCsv.replace(/^\uFEFF/, "").split("\r\n").slice(0, 2);
ok(/^obrabotka_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.csv$/.test(suggested), `файл: ${suggested}`);
ok(rawCsv.startsWith("\uFEFF") && head[0].includes(";") && head[0].startsWith("Дата"), `шапка CSV: ${head[0]}`);
ok(head[1]?.includes(","), `десятичный разделитель в Excel-строке: ${head[1]}`);

// другие вклады отчётов
for (const tab of ["Итоги по складам", "Остатки грузов", "Загрузка складов"]) {
  await page.click(`text=${tab}`);
  await page.waitForTimeout(600);
  const n = await page.$$eval("table tbody tr", (r) => r.length);
  ok(n > 0, `вкладка «${tab}»: ${n} строк`);
}
await page.screenshot({ path: "shot_reports.png", fullPage: true });

// фильтры отчётов тоже запоминаются
await page.click("text=90 дней");
await page.waitForTimeout(900);
await page.reload({ waitUntil: "networkidle" });
await page.click("nav >> text=Отчёты");
await page.waitForSelector("table tfoot tr", { timeout: 10000 });
const active = await page.$eval("button.chip.bg-amber2\\/20", (e) => e.textContent).catch(() => "");
ok(active.includes("90"), `фильтр периода запомнился (${active.trim()})`);

console.log(problems.length ? `\nПРОБЛЕМЫ:\n${problems.join("\n")}` : "\nВсе проверки пройдены");
await browser.close();
process.exit(problems.length ? 1 : 0);
