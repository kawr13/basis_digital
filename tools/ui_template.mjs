/**
 * Браузерная проверка «Судно-шаблон»: из окна «Новая постановка» копируем
 * прошлую постановку, правим только дату. Даты — то, ради чего шаблон и
 * берётся, поэтому после выбора они обязаны быть новыми.
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> [BASE=...] [PASS=...] node tools/ui_template.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://localhost:5173";
const PASS = process.env.PASS || "Perf-2026-pass";
const problems = [];
const ok = (cond, msg) => { console.log(`${cond ? "✅" : "❌"} ${msg}`); if (!cond) problems.push(msg); };
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
try {
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const out = page.locator("button:has-text('Выйти')");
  if (await out.count()) { await out.first().click(); await page.waitForTimeout(400); }
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("input[autocomplete=username]", "admin");
  await page.fill("input[autocomplete=current-password]", PASS);
  await page.click("button.btn-primary");
  await page.waitForSelector("button:has-text('Выйти')", { timeout: 15000 });

  await page.goto(`${BASE}/vessels`, { waitUntil: "networkidle" });
  await page.click("button:has-text('Новая постановка')");
  await page.waitForSelector("[data-modal=placement-form]");
  ok(await page.locator("button:has-text('Скопировать из предыдущей постановки…')").count() === 1,
     "в окне создания есть кнопка «Скопировать из предыдущей постановки…»");

  await page.click("[data-admin-action=template-pick]");
  await page.waitForSelector("[data-modal=template-list] button", { timeout: 8000 });
  const rows = await page.locator("[data-modal=template-list] [data-admin-action=template-pick-one]").count();
  ok(rows >= 3, `шаблонов загружено: ${rows}`);

  await page.fill("[data-field=template-search]", "дон");
  await page.waitForTimeout(150);
  const found = await page.locator("[data-modal=template-list] [data-admin-action=template-pick-one]").count();
  ok(found >= 1, `поиск «дон» нашёл строк: ${found}`);
  await page.screenshot({ path: "D:/tmp/tpl_picker.png" });

  await page.locator("[data-modal=template-list] [data-admin-action=template-pick-one]").first().click();
  await page.waitForSelector("[data-field=vessel]");
  const vessel = await page.inputValue("[data-field=vessel]");
  const startDate = await page.inputValue("[data-field=start-date]");
  const length = await page.inputValue("[data-field=length-m]");
  ok(vessel.toLocaleUpperCase("ru-RU").includes("ДОН"), `судно подставлено: «${vessel}»`);
  ok(startDate === ymd(new Date()), `дата начала — сегодня (${startDate})`);
  ok(length !== "", `длина продублирована: ${length} м`);
  await page.screenshot({ path: "D:/tmp/tpl_filled.png" });

  // «Назад к форме» не должен терять то, что уже подставили
  await page.click("[data-admin-action=template-pick]");
  await page.waitForSelector("[data-modal=template-list]");
  await page.click("[data-admin-action=template-back]");
  ok((await page.inputValue("[data-field=vessel]")) === vessel, "возврат из выбора шаблона форму не сбросил");

  // в окне правки кнопки-шаблона быть не должно: открываем детали любой
  // постановки и жмём «Править»
  await page.click("button:has-text('Отмена')");
  await page.waitForTimeout(300);
  await page.locator("button[title*='ДОН'], button[title*='ДОН']").first().click();
  await page.waitForSelector("[data-admin-action=edit-placement]", { timeout: 5000 });
  await page.click("[data-admin-action=edit-placement]");
  await page.waitForSelector("[data-modal=placement-form]");
  const editOpen = await page.locator("[data-modal=placement-form] [data-admin-action=template-pick]").count();
  ok(editOpen === 0, "в окне правки чужая кнопка «скопировать» не показывается");
  await page.click("button:has-text('Отмена')");
} finally {
  await browser.close();
}
console.log(problems.length ? `ПРОБЛЕМЫ: ${problems.length}` : "все проверки прошли");
process.exit(problems.length ? 1 : 0);
