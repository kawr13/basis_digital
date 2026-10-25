/**
 * Проверка «почти реального времени»: кабинет сам замечает новый срез из 1С
 * и перезапрашивает данные без перезагрузки страницы.
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> [BASE=...] [PASS=...] node tools/ui_live.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://localhost:5173";
const API = process.env.API || "http://127.0.0.1:8000";
const PASS = process.env.PASS || "Top-Secret-2026";

const problems = [];
const ok = (cond, msg) => {
  console.log(`${cond ? "✅" : "❌"} ${msg}`);
  if (!cond) problems.push(msg);
};

// --- API-клиент теста: логинимся отдельно от страницы ------------------------
const loginRes = await fetch(`${API}/api/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ login: "admin", password: PASS }),
});
if (!loginRes.ok) {
  console.log("❌ не удалось войти в API для проверки:", loginRes.status);
  process.exit(1);
}
const { access_token: token } = await loginRes.json();
const call = (path, init = {}) =>
  fetch(`${API}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(init.headers || {}) },
  });

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
page.on("pageerror", (e) => problems.push(`JS: ${e.message}`));

// access-токен живёт в памяти приложения, поэтому смотрим не «своим» fetch'ем,
// а перехватываем ответы самого кабинета
const freshnessReqs = [];
const summaryReqs = [];
let lastFreshness = null;
let lastPublic = null;
page.on("request", (r) => {
  if (r.url().includes("/api/meta/freshness")) freshnessReqs.push(Date.now());
  if (r.url().includes("/api/metrics/summary")) summaryReqs.push(Date.now());
});
page.on("response", async (r) => {
  try {
    if (r.url().includes("/api/meta/freshness") && r.status() === 200) lastFreshness = await r.json();
    if (r.url().includes("/api/settings/public") && r.status() === 200) lastPublic = await r.json();
  } catch {
    /* не-json — не важно */
  }
});

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.fill("input[autocomplete=username]", "admin");
await page.fill("input[autocomplete=current-password]", PASS);
await page.click("button.btn-primary");
await page.waitForSelector("button:has-text('Выйти')", { timeout: 15000 });

// «Статистика» стала главной страницей, витрина складов 1С — на вкладке «Склады 1С».
await page.goto(`${BASE}/warehouses`, { waitUntil: "networkidle" });
await page.waitForSelector("[data-live]", { timeout: 15000 });

// --- 1. чип живого статуса на месте -----------------------------------------
const age = await page.getAttribute("[data-live]", "data-live-age");
ok(age !== null && Number(age) >= 0, `чип свежести показывает возраст: ${age} с`);

// --- 2. бэкенд и DOM говорят одно и то же -----------------------------------
await page.waitForFunction(() => true).catch(() => {});
for (let i = 0; i < 20 && !lastFreshness; i++) await page.waitForTimeout(500);
ok(!!lastFreshness && typeof lastFreshness.stale === "boolean",
   `кабинет получил свежесть от бэкенда: stale=${lastFreshness?.stale}, срез ${lastFreshness?.data_as_of}`);
const staleBadges = await page.locator("[data-stale]").count();
ok(
  lastFreshness?.stale ? staleBadges > 0 : staleBadges === 0,
  `бейдж «данные устарели» (${staleBadges} шт.) соответствует ответу бэкенда`,
);
const apiFresh = await call("/api/meta/freshness").then((r) => r.json());
ok(apiFresh.data_as_of === lastFreshness?.data_as_of, "DOM и API называют одну и ту же метку среза");

// --- 3. опрос действительно ходит (тик 15 с) ---------------------------------
const before = freshnessReqs.length;
await page.waitForTimeout(17000);
ok(freshnessReqs.length > before, `фронт опрашивает свежесть: запросов ${before} → ${freshnessReqs.length}`);

// --- 4. новый срез из 1С подтягивается сам, без перезагрузки -----------------
// метка среза у мок-источника округлена до минуты: ждём перехода минуты,
// иначе новый синк в той же минуте ничего бы не изменил
const waitToNextMinute = Math.max(1000, 61_000 - (Date.now() % 60_000)) + 1500;
console.log(`… ждём ${Math.round(waitToNextMinute / 1000)} с до начала следующей минуты`);
await page.waitForTimeout(waitToNextMinute);

const sync = await call("/api/admin/sync/run", { method: "POST" });
ok(sync.ok, `ручная загрузка из 1С через API: HTTP ${sync.status}`);
const syncBody = await sync.json().catch(() => ({}));
console.log(`   загружено строк: ${syncBody?.counts?.rows ?? syncBody?.rows}`);

const summaryBefore = summaryReqs.length;
let refetched = false;
for (let i = 0; i < 45 && !refetched; i++) {
  await page.waitForTimeout(1000);
  if (summaryReqs.length > summaryBefore) refetched = true;
}
ok(refetched, `дашборд сам перезапросил метрики после нового среза (${summaryBefore} → ${summaryReqs.length})`);
if (refetched) {
  const note = await page.locator("[data-live-note]").count();
  ok(note === 1, `появилась пометка «данные обновлены» (элементов: ${note})`);
}

// --- 5. настройка периода автообновления доходит до фронта -------------------
const put = await call("/api/admin/settings", {
  method: "PUT",
  body: JSON.stringify({ "ui.refresh_seconds": 30 }),
});
ok(put.status === 200, `сохранение ui.refresh_seconds: HTTP ${put.status}`);
await page.reload({ waitUntil: "networkidle" });
for (let i = 0; i < 20 && !lastPublic; i++) await page.waitForTimeout(500);
ok(lastPublic?.["ui.refresh_seconds"] === 30, `фронт видит ui.refresh_seconds=${lastPublic?.["ui.refresh_seconds"]}`);
await call("/api/admin/settings/reset", { method: "POST", body: JSON.stringify(["ui.refresh_seconds"]) });

await browser.close();

console.log(problems.length ? `\nПРОБЛЕМЫ:\n${problems.join("\n")}` : "\nвсе проверки прошли");
process.exit(problems.length ? 1 : 0);
