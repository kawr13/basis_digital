/**
 * Проверка страницы входа: геометрия синей панели (логотип в левом верхнем углу,
 * заголовок ровно по середине высоты) и то, что заголовок раскрашен как логотип.
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> node tools/ui_login.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://localhost:5173";
const problems = [];
const ok = (cond, msg) => {
  console.log(cond ? `✅ ${msg}` : `❌ ${msg}`);
  if (!cond) problems.push(msg);
};

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => ok(false, `ошибка страницы: ${e.message}`));

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });

const geo = await page.evaluate(() => {
  const panel = document.querySelector("aside");
  const logo = panel.querySelector("img");
  const title = panel.querySelector("h1");
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, w: r.width, h: r.height };
  };
  const spans = [...title.querySelectorAll("span")].map((s) => ({
    text: s.textContent,
    color: getComputedStyle(s).color,
  }));
  return {
    panel: box(panel),
    logo: box(logo),
    title: box(title),
    spans,
    body: document.body.innerText,
  };
});

// панель p-12 = 48px отступ; логотип — в левом верхнем углу
ok(geo.logo.left >= geo.panel.left + 44 && geo.logo.left <= geo.panel.left + 52,
  `логотип прижат к левому краю панели (left=${Math.round(geo.logo.left - geo.panel.left)}px от неё)`);
ok(geo.logo.top >= geo.panel.top + 44 && geo.logo.top <= geo.panel.top + 52,
  `логотип вверху панели (top=${Math.round(geo.logo.top - geo.panel.top)}px от неё)`);
ok(geo.logo.h <= 32, `логотип маленький: ${Math.round(geo.logo.h)}px в высоту`);
// знак 230×35; в column-flex контейнере align-items:stretch растягивает img на всю
// ширину панели, и логотип начинает выглядеть размазанным посередине
const ratio = geo.logo.w / geo.logo.h;
ok(Math.abs(ratio - 230 / 35) < 0.2, `пропорция логотипа сохранена: ${ratio.toFixed(2)} против ${(230 / 35).toFixed(2)}`);
ok(geo.logo.right < geo.panel.right - 200,
  `логотип не растянут по панели: занимает ${Math.round(geo.logo.w)}px из ${Math.round(geo.panel.w)}px`);

const panelMid = geo.panel.top + geo.panel.h / 2;
const titleMid = geo.title.top + geo.title.h / 2;
ok(Math.abs(titleMid - panelMid) <= 6,
  `заголовок по середине высоты панели: центр ${Math.round(titleMid)}px против ${Math.round(panelMid)}px (разница ${Math.round(titleMid - panelMid)}px)`);

// раскраска «как логотип»: белое первое слово, голубое второе (#26AAE1 из знака)
const white = geo.spans.find((s) => s.text === "Basis");
const blue = geo.spans.find((s) => s.text === ".Indicators");
ok(!!white && !!blue, "заголовок разбит на Basis и .Indicators");
ok(white?.color === "rgb(255, 255, 255)", `«Basis» белый (${white?.color})`);
ok(blue?.color === "rgb(38, 170, 225)", `«${blue?.text}» голубым знака ${blue?.color}`);

// лишнего текста на входе быть не должно
ok(!/Остатки грузов, загрузка складов/.test(geo.body), "описания под заголовком нет");
ok(!/Ёмкость|занято \/ доступно/i.test(geo.body), "тройки «Остатки / Ёмкость / Динамика» нет");

await page.screenshot({ path: "shot_login_panel.png" });
await browser.close();

console.log(problems.length ? `\nПРОБЛЕМЫ:\n${problems.join("\n")}` : "\nвсе проверки прошли");
process.exit(problems.length ? 1 : 0);
