/**
 * Проверка вычисленными стилями: матовое стекло и новый график.
 * Скриншоты не смотрим — только computed styles (см. AGENTS.md про vite/tailwind).
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://127.0.0.1:5173";
const PASS = process.env.PASS || "Top-Secret-2026";
const problems = [];
const ok = (cond, msg) => console.log(`${cond ? "✅" : "❌"} ${msg}`) || (cond || problems.push(msg));

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => problems.push(`JS: ${e.message}`));

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill("input[autocomplete=username]", "admin");
await page.fill("input[autocomplete=current-password]", PASS);
await page.click("button.btn-primary");
await page.waitForSelector("section[data-widget]", { timeout: 10000 });
await page.waitForTimeout(900); // даём анимации графика закончиться

const cs = (sel, props) =>
  page.$eval(sel, (el, p) => {
    const s = getComputedStyle(el);
    const out = {};
    for (const k of p) out[k] = s[k];
    return out;
  }, props);

// ---- 1. фон страницы: база + 5 слоёв (3 пятна + 2 сетки), fixed ----
const body = await cs("body", ["backgroundColor", "backgroundImage", "backgroundAttachment"]);
ok(body.backgroundColor === "rgb(232, 238, 247)", `body background-color = ${body.backgroundColor}`);
ok(
  body.backgroundImage.split("radial-gradient").length - 1 === 3 &&
    body.backgroundImage.split("repeating-linear-gradient").length - 1 === 2,
  `body слоёв: ${(body.backgroundImage.match(/gradient/g) || []).length} (3 пятна + 2 сетки)`,
);
ok(/^(fixed)(,\s*fixed){4}$/.test(body.backgroundAttachment), `body background-attachment = ${body.backgroundAttachment}`);

// ---- 2. плашка = матовое стекло ----
const panel = await cs("section[data-widget=\"dynamics\"]", [
  "backgroundColor", "backgroundImage", "backdropFilter", "borderTopColor", "borderTopWidth", "boxShadow",
]);
const panelAlpha = panel.backgroundColor.startsWith("rgba")
    ? Number(panel.backgroundColor.split("(")[1].replace(")", "").split(",")[3].trim())
    : 1;
ok(panelAlpha > 0.3 && panelAlpha <= 0.62, `.panel полупрозрачная (alpha=${panelAlpha})`);
ok(/linear-gradient\(160deg/.test(panel.backgroundImage), `.panel gradient: ${panel.backgroundImage.slice(0, 64)}…`);
ok(
  /^blur\((1[6-9]|2\d|3\d)px\)/.test(panel.backdropFilter) && /saturate/.test(panel.backdropFilter),
  `.panel backdrop-filter = ${panel.backdropFilter}`,
);
ok(/rgba\(255, 255, 255, 0\.[5-7]/.test(panel.borderTopColor), `.panel border-color = ${panel.borderTopColor}`);
ok(
  /rgba\(255, 255, 255, 0\.9\) 0px 1px 0px 0px inset/.test(panel.boxShadow),
  `внутренняя подсветка в тени: …${panel.boxShadow.slice(-46)}`,
);

// ---- 3. шапка = glass-navy ----
const head = await cs("header", ["backgroundColor", "backdropFilter", "borderBottomColor", "position"]);
ok(head.backgroundColor === "rgba(27, 51, 96, 0.82)", `header background = ${head.backgroundColor}`);
ok(head.backdropFilter === "blur(16px) saturate(1.4)", `header backdrop-filter = ${head.backdropFilter}`);
ok(
  head.borderBottomColor === "rgba(47, 186, 236, 0.35)",
  `header нижняя рамка = ${head.borderBottomColor}`,
);

// ---- 4. график: path-столбцы, сглаженная линия, заливка, анимация отыграла ----
const chart = await page.evaluate(() => {
  const svg = document.querySelector('section[data-widget="dynamics"] svg');
  const bars = [...svg.querySelectorAll("path.dc-bar")];
  const line = svg.querySelector("path.dc-line");
  const area = svg.querySelector("path.dc-area");
  const hits = [...svg.querySelectorAll("rect.dc-hit[data-bar]")];
  const grid = [...svg.querySelectorAll("line")].filter((l) => l.getAttribute("stroke-dasharray") === "3 4");
  return {
    bars: bars.length,
    barD: bars[0]?.getAttribute("d") ?? "",
    barTransform: bars[0] ? getComputedStyle(bars[0]).transform : "",
    barTransition: bars[0] ? getComputedStyle(bars[0]).transitionProperty : "",
    lineD: line?.getAttribute("d") ?? "",
    lineDash: line ? getComputedStyle(line).strokeDashoffset : "",
    pathLength: line?.getAttribute("pathLength") ?? "",
    areaFill: area ? getComputedStyle(area).fill : "",
    areaOpacity: area ? getComputedStyle(area).opacity : "",
    hits: hits.length,
    grid: grid.length,
    rightLabels: [...svg.querySelectorAll("text")].filter((t) => /ч$/.test(t.textContent)).length,
  };
});
ok(chart.bars === 14 && /Z$/.test(chart.barD), `столбцы — path со скруглением сверху (${chart.bars} шт.)`);
ok(chart.barTransform === "none" || /matrix\(1, 0, 0, 1/.test(chart.barTransform), `анимация роста отыграна: ${chart.barTransform}`);
ok(chart.barTransition.includes("transform"), `переход столбца: ${chart.barTransition}`);
ok(/C-?\d/.test(chart.lineD), `линия хранения — кубический сплейн (есть C-сегменты)`);
ok(chart.pathLength === "1" && chart.lineDash === "0px", `линия дорисована (pathLength=${chart.pathLength}, dashoffset=${chart.lineDash})`);
ok(/^url\(/.test(chart.areaFill) && chart.areaOpacity === "1", `заливка под линией: ${chart.areaFill}`);
ok(chart.grid === 3, `сетка из 3 пунктирных линий: ${chart.grid}`);
ok(chart.rightLabels === 3, `справа шкала часов: ${chart.rightLabels} подписей`);
ok(chart.hits === 14, `зон клика по-прежнему 14`);

// ---- 5. тултип + перекрестие по наведению мыши ----
const bar = await page.$('section[data-widget="dynamics"] rect[data-bar]');
await bar.hover();
await page.waitForTimeout(150);
const tip = await page.evaluate(() => {
  const t = document.querySelector("[data-chart-tip]");
  const svg = document.querySelector('section[data-widget="dynamics"] svg');
  const cross = [...svg.querySelectorAll("line")].find((l) => l.getAttribute("stroke") === "rgba(27,51,96,.25)");
  return {
    text: t?.textContent ?? "",
    pe: t ? getComputedStyle(t).pointerEvents : "",
    bg: t ? getComputedStyle(t).backdropFilter : "",
    pos: t ? getComputedStyle(t).position : "",
    cross: !!cross,
    dot: !!svg.querySelector("circle"),
  };
});
ok(/обработано/.test(tip.text) && /принято/.test(tip.text) && /отгружено/.test(tip.text) && /документов/.test(tip.text) && /ср\. хранение/.test(tip.text),
  `тултип: ${tip.text.replace(/\s+/g, " ").slice(0, 96)}…`);
ok(tip.pe === "none" && tip.pos === "absolute", `тултип: position=${tip.pos}, pointer-events=${tip.pe}`);
ok(tip.bg === "blur(12px) saturate(1.5)", `тултип — стекло: ${tip.bg}`);
ok(tip.cross && tip.dot, "перекрестие + точка на линии хранения");

// ---- 6. тултип с клавиатуры ----
await page.evaluate(() => document.querySelector('rect[data-bar]').focus());
await page.waitForTimeout(120);
ok(
  await page.evaluate(() => document.activeElement?.getAttribute("data-bar") === document.querySelector("[data-chart-tip]")?.getAttribute("data-chart-tip")),
  "при фокусе с клавиатуры тултип показывает те же сутки",
);

// ---- 7. легенда-пилюли: подсветка ряда ----
const legend = await page.evaluate(() => {
  const pills = [...document.querySelectorAll(".dc-pill")];
  const groups = [...document.querySelectorAll('section[data-widget="dynamics"] g.dc-series')];
  return { pills: pills.length, before: groups.map((g) => getComputedStyle(g).opacity) };
});
await page.hover(".dc-pill[data-legend=\"bars\"]");
await page.waitForTimeout(250);
const after = await page.$$eval('section[data-widget="dynamics"] g.dc-series', (gs) =>
  gs.map((g) => getComputedStyle(g).opacity),
);
ok(legend.pills === 3, `пилюль легенды: ${legend.pills}`);
ok(
  after.some((o) => o === "1") && after.filter((o) => o === "0.35").length === 2,
  `наведение на пилюлю: было ${legend.before.join(",")} → стало ${after.join(",")}`,
);
await page.mouse.move(10, 10);

// ---- 8. prefers-reduced-motion: анимации нет ----
const rmCtx = await browser.newContext({ viewport: { width: 1200, height: 800 } });
await rmCtx.addCookies([]);
const rm = await rmCtx.newPage();
await rm.emulateMedia({ reducedMotion: "reduce" });
await rm.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await rm.fill("input[autocomplete=username]", "admin");
await rm.fill("input[autocomplete=current-password]", PASS);
await rm.click("button.btn-primary");
await rm.waitForSelector('section[data-widget="dynamics"] path.dc-bar', { timeout: 10000 });
const rmState = await rm.evaluate(() => {
  const b = document.querySelector('section[data-widget="dynamics"] path.dc-bar');
  const l = document.querySelector("path.dc-line");
  return {
    bt: getComputedStyle(b).transform,
    bd: getComputedStyle(b).transitionDuration,
    lo: getComputedStyle(l).strokeDashoffset,
  };
});
ok(
  rmState.bt === "none" && rmState.bd === "0s" && rmState.lo === "0px",
  `prefers-reduced-motion: transform=${rmState.bt}, duration=${rmState.bd}, dashoffset=${rmState.lo}`,
);
await rm.close();
await rmCtx.close();

// ---- 10. спарклайн в KPI-карточке обработки (проверяем на дашборде) ----
const spark = await page.evaluate(() => {
  const s = document.querySelector('[data-sparkline]');
  return s ? { n: s.getAttribute("data-sparkline"), h: s.getBoundingClientRect().height, poly: !!s.querySelector("polyline") } : null;
});
ok(spark && spark.n === "14" && Math.round(spark.h) === 32 && spark.poly,
  `спарклайн в KPI: ${spark ? `${spark.n} значений, ${Math.round(spark.h)} px` : "нет"}`);

// ---- 9. модалка админки: подложка + стекло ----
await page.goto(`${BASE}/admin`, { waitUntil: "networkidle" });
await page.waitForSelector('[data-admin-action="create-user"]', { timeout: 8000 });
await page.click('[data-admin-action="create-user"]');
await page.waitForSelector("[data-modal-overlay]", { timeout: 5000 });
const modal = await page.evaluate(() => {
  const o = document.querySelector("[data-modal-overlay]");
  const m = document.querySelector("[data-modal]");
  return {
    ov: getComputedStyle(o).backgroundColor,
    blur: getComputedStyle(m).backdropFilter,
    bg: getComputedStyle(m).backgroundColor,
  };
});
ok(modal.ov === "rgba(15, 27, 45, 0.35)", `подложка модалки = ${modal.ov}`);
ok(/blur\((1[6-9]|2\d|3\d)px\)/.test(modal.blur) && /rgba\(255, 255, 255, 0\.[0-6]/.test(modal.bg),
  `модалка = стекло: ${modal.blur}, ${modal.bg}`);

await browser.close();
console.log(problems.length ? `\nПРОБЛЕМЫ:\n- ${problems.join("\n- ")}` : "\nвсе проверки стиля прошли");
process.exit(problems.length ? 1 : 0);
