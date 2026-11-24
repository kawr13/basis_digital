/**
 * Проверка, что кабинет работает в браузере без `AbortSignal.any`
 * (Chrome/Edge < 116, Firefox < 124, Safari < 17.4): раньше api.ts звал его
 * безусловно, и там падал каждый запрос — «AbortSignal.any is not a function».
 *
 * Запуск: PW_CHROMIUM="C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" node tools/ui_legacy_abort.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://localhost:5173";
const problems = [];
const ok = (cond, msg) => {
  console.log(cond ? `  ✅ ${msg}` : `  ❌ ${msg}`);
  if (!cond) problems.push(msg);
};

const login = async (page) => {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.locator('input:not([type="password"])').first().fill("petrov");
  await page.locator('input[type="password"]').first().fill("demo1234");
  await page.click("button.btn-primary");
  await page.waitForFunction(() => !location.pathname.startsWith("/login"), { timeout: 20000 });
};

const modes = [
  { name: "нет AbortSignal.any (Chrome 100…115)", init: () => { delete AbortSignal.any; }, any: "absent", timeout: "native" },
  { name: "нет ни AbortSignal.any, ни AbortSignal.timeout", init: () => { delete AbortSignal.any; delete AbortSignal.timeout; }, any: "absent", timeout: "absent" },
  // нативная ветка тоже обязана работать: правка не должна была её сломать
  { name: "современный браузер: оба метода на месте", init: () => {}, any: "native", timeout: "native" },
];
const state = (v, want) => (v === "function") === (want === "native");
const SKIP_TIMEOUT = process.env.SKIP_TIMEOUT === "1";

for (const mode of modes) {
  console.log(`\n=== ${mode.name} ===`);
  const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  // замедляем API: только так видно, что прежний запрос реально отменяется,
  // а не успевает отработаться до клика по следующему периоду
  await ctx.route("**/api/**", async (r) => {
    await new Promise((res) => setTimeout(res, 600));
    await r.fallback();
  });
  await ctx.addInitScript(mode.init);
  const page = await ctx.newPage();
  const errors = [];
  const api200 = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() === 200) api200.push(new URL(r.url()).pathname);
  });

  await login(page);
  const api = await page.evaluate(() => ({ any: typeof AbortSignal.any, timeout: typeof AbortSignal.timeout }));
  ok(state(api.any, mode.any), `AbortSignal.any ${mode.any === "absent" ? "отсутствует" : "нативный"} (${api.any})`);
  ok(state(api.timeout, mode.timeout), `AbortSignal.timeout ${mode.timeout === "absent" ? "отсутствует" : "нативный"} (${api.timeout})`);

  await page.waitForTimeout(2500);
  ok(api200.length >= 3, `запросов к API отработало: ${api200.length} (${[...new Set(api200)].slice(0, 3).join(", ")})`);
  ok(errors.length === 0, `ошибок страницы нет${errors.length ? `: ${errors.slice(0, 2).join(" | ")}` : ""}`);

  // смена периода — прежний запрос обязан быть отменён, а не дописан поверх
  const res = await page.evaluate(async () => {
    const aborted = [];
    const orig = window.fetch;
    window.fetch = (url, init) => {
      const p = orig(url, init);
      if (init?.signal) init.signal.addEventListener("abort", () => aborted.push(String(url)));
      return p;
    };
    const chips = [...document.querySelectorAll("button")].filter((b) => /^(7|14|30|90) сут$|^Все смены$/.test((b.textContent || "").trim()));
    for (const c of chips) { c.click(); await new Promise((r) => setTimeout(r, 150)); }
    await new Promise((r) => setTimeout(r, 1200));
    window.fetch = orig;
    return { aborted, chips: chips.length };
  });
  ok(res.chips >= 3, `чипов периода найдено: ${res.chips}`);
  ok(res.aborted.length > 0, `отмена прежних запросов работает: отменено ${res.aborted.length}`);
  ok(errors.length === 0, `после смены периода ошибок нет${errors.length ? `: ${errors.slice(0, 2).join(" | ")}` : ""}`);

  await browser.close();
}

// таймаут в «старом» браузере: висящий бэкенд обязан дать плашку 408, а не спиннер
if (!SKIP_TIMEOUT) console.log("\n=== таймаут без нативного AbortSignal.timeout (бэкенд висит) ===");
if (!SKIP_TIMEOUT) {
  const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => { delete AbortSignal.any; delete AbortSignal.timeout; });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  // Висящий бэкенд: все ручки кроме авторизации молчат вечно. Один обработчик,
  // потому что при двух `fallback` цепочка сама же себя и вешала бы.
  await ctx.route("**/api/**", (r) => {
    if (new URL(r.request().url()).pathname.startsWith("/api/auth/")) return r.fallback();
    // молчим: запрос обязан умереть по таймауту кабинета, а не по вине сети
  });
  // Зовём API-слой прямо на странице (тот же модуль, что у приложения): плашка
  // плитки — вопрос отрисовки, а здесь важна сама склейка «таймаут без
  // нативного AbortSignal.timeout». Висящий запрос обязан умереть за 30 с
  // понятной ошибкой, а не держать спиннер.
  const res = await page.evaluate(async () => {
    const mod = await import("/src/api.ts");
    const t0 = performance.now();
    try {
      await mod.api.summary();
      return { reached: true, ms: Math.round(performance.now() - t0) };
    } catch (e) {
      return { reached: false, message: String(e?.message), ms: Math.round(performance.now() - t0) };
    }
  });
  ok(!res.reached && /Сервер не ответил за 30 с/.test(res.message || ""),
    `висящий запрос умер по таймауту за ${res.ms} мс: «${res.message}»`);
  ok(res.ms > 28_000 && res.ms < 34_000, `таймер сработал на 30-й секунде, а не раньше: ${res.ms} мс`);
  ok(errors.length === 0, `ошибок страницы нет${errors.length ? `: ${errors.slice(0, 2).join(" | ")}` : ""}`);
  await browser.close();
}

console.log(problems.length ? `\nПРОБЛЕМЫ:\n${problems.join("\n")}` : "\nвсе проверки прошли");
process.exit(problems.length ? 1 : 0);
