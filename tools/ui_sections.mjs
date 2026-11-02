// Смок «скрытые разделы кабинета»: админ прячет/показывает разделы в
// настройках, шапка и маршруты реагируют без перезагрузки, «спрятать всё»
// бэк не даёт.
//
// Запуск:  node tools/ui_sections.mjs
// Переменные: PW_CHROMIUM, PASS (пароль админа), BASE (по умолчанию 5173).
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://127.0.0.1:5173";
const PASS = process.env.PASS || "Top-Secret-2026";
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });

const failures = [];
const ok = (cond, text) => {
  console.log(`${cond ? "✅" : "❌"} ${text}`);
  if (!cond) failures.push(text);
};

page.on("pageerror", (e) => failures.push("PAGE ERR: " + e.message));
page.on("response", (r) => {
  if (r.status() >= 500) failures.push(`HTTP ${r.status()}: ${r.url().slice(0, 100)}`);
});

const navTabs = () => page.$$eval("header nav a", (els) => els.map((e) => e.textContent?.trim()));
const box = (choice) => `label:has(> input[data-choice="${choice}"])`;

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill("input[autocomplete=username]", "admin");
await page.fill("input[autocomplete=current-password]", PASS);
await page.click("button.btn-primary");
await page.waitForSelector("[data-stats-toolbar]", { timeout: 15000 });
await page.waitForTimeout(1200);

// ---- админка: форма «Отображение разделов» ----------------------------------
await page.goto(`${BASE}/admin`, { waitUntil: "networkidle" });
await page.click("text=Настройки");
await page.waitForSelector('input[data-setting="ui.hidden_sections"]', { timeout: 10000 });
const labels = await page.$$eval('input[data-setting="ui.hidden_sections"]', (els) =>
  els.map((e) => e.closest("label")?.textContent?.trim()));
ok(labels.join(",") === "Индикаторы,Склады 1С,Отчёты,Постановка",
   `в форме четыре раздела: ${labels.join(", ")}`);

// идемпотентность: если прошлый прогон прервался на середине, галки могли
// остаться — перед тестом приводим форму к «ничего не скрыто»
for (const c of ["statistics", "warehouses", "reports", "vessels"]) {
  if (await page.isChecked(box(c))) await page.uncheck(box(c));
}
await page.click('[data-admin-action="save-settings"]');
await page.waitForTimeout(1200);

await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.waitForTimeout(900);
const before = await navTabs();
ok(before.includes("Индикаторы") && before.includes("Склады 1С") && before.includes("Постановка"),
   `все разделы видимы по умолчанию: ${before.join(", ")}`);

// ---- прячем «Склады 1С» и «Постановку» --------------------------------------
await page.goto(`${BASE}/admin`, { waitUntil: "networkidle" });
await page.click("text=Настройки");
await page.waitForSelector('input[data-setting="ui.hidden_sections"]', { timeout: 10000 });
await page.check(box("warehouses"));
await page.check(box("vessels"));
await page.click('[data-admin-action="save-settings"]');
await page.waitForTimeout(1500);
const hidden = await navTabs();
ok(!hidden.includes("Склады 1С") && !hidden.includes("Постановка"),
   `скрытые разделы исчезли из шапки без перезагрузки: ${hidden.join(", ")}`);
ok(hidden.includes("Индикаторы") && hidden.includes("Администрирование"),
   "остальные разделы на месте");

// прямой адрес скрытого раздела ведёт на первый видимый
await page.goto(`${BASE}/warehouses`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
ok(new URL(page.url()).pathname === "/", `/warehouses редиректит на первый видимый: ${page.url()}`);
await page.goto(`${BASE}/vessels`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
ok(new URL(page.url()).pathname === "/", `/vessels редиректит на первый видимый: ${page.url()}`);

// ---- возвращаем ---------------------------------------------------------------
await page.goto(`${BASE}/admin`, { waitUntil: "networkidle" });
await page.click("text=Настройки");
await page.waitForSelector('input[data-setting="ui.hidden_sections"]', { timeout: 10000 });
ok((await page.isChecked(box("warehouses"))) === true, "черновик помнит скрытый раздел");
await page.uncheck(box("warehouses"));
await page.uncheck(box("vessels"));
await page.click('[data-admin-action="save-settings"]');
await page.waitForTimeout(1500);
const restored = await navTabs();
ok(restored.includes("Склады 1С") && restored.includes("Постановка"),
   `разделы вернулись: ${restored.join(", ")}`);

// ---- «спрятать всё» бэк не даёт ----------------------------------------------
for (const c of ["statistics", "warehouses", "reports", "vessels"]) await page.check(box(c));
await page.click('[data-admin-action="save-settings"]');
await page.waitForSelector('[data-form-error]', { timeout: 10000 });
const formError = await page.$eval('[data-form-error]', (e) => e.textContent || "");
ok(/хотя бы один/.test(formError), `ошибка «нельзя скрыть все разделы» показана: ${formError.trim()}`);
// откат — снятием галок, а не «Сбросить к значениям по умолчанию»:
// тот сбрасывает ВСЕ переопределённые настройки, включая dispatcher.source_path
for (const c of ["statistics", "warehouses", "reports", "vessels"]) await page.uncheck(box(c));
await page.click('[data-admin-action="save-settings"]');
await page.waitForTimeout(1500);
const final = await navTabs();
ok(final.includes("Индикаторы") && final.includes("Склады 1С") && final.includes("Отчёты") && final.includes("Постановка"),
   `после сброса все разделы видимы: ${final.join(", ")}`);

if (failures.length) {
  console.log(`\nПРОБЛЕМЫ (${failures.length}):`);
  for (const f of failures) console.log("- " + f);
  process.exit(1);
}
console.log("\nВсе проверки прошли");
await browser.close();
