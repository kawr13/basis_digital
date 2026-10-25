/** Скриншоты кабинета: вход и дашборд (для визуальной проверки темы). */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://127.0.0.1:5173";
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
page.on("pageerror", (e) => console.log("PAGE ERR:", e.message));

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.screenshot({ path: "shot_theme_login.png" });

await page.fill("input[autocomplete=username]", "admin");
await page.fill("input[autocomplete=current-password]", process.env.PASS || "Top-Secret-2026");
await page.click("button.btn-primary");
await page.waitForSelector("section[data-widget]", { timeout: 10000 });

// «Статистика» стала главной страницей, витрина складов 1С — на вкладке «Склады 1С».
await page.goto(`${BASE}/warehouses`, { waitUntil: "networkidle" });
await page.waitForSelector("section[data-widget]", { timeout: 10000 });
await page.waitForTimeout(700);
await page.screenshot({ path: "shot_theme_dashboard.png" });

await page.click("nav >> text=Отчёты");
await page.waitForSelector("table tfoot tr", { timeout: 10000 });
await page.screenshot({ path: "shot_theme_reports.png" });

await browser.close();
console.log("ok");
