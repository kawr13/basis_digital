/**
 * Браузерная проверка личных вкладок: админ галками в карточке пользователя
 * оставляет ему только «Индикаторы» — у пользователя исчезают вкладки,
 * а прямые адреса ведут на первую видимую.
 *
 * Запуск: PW_CHROMIUM=<chrome> [BASE=...] [API=...] [PASS=...] node tools/ui_user_sections.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://localhost:5173";
const API = process.env.API || "http://127.0.0.1:8000";
const PASS = process.env.PASS || "";
if (!PASS) { console.error("Нужна переменная PASS (пароль админа кабинета)"); process.exit(2); }
const LOGIN = `sectest${Date.now() % 100000}`;
const USERPASS = "Sect-Test-2026-x";
const problems = [];
const ok = (c, m) => { console.log(`${c ? "✅" : "❌"} ${m}`); if (!c) problems.push(m); };

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const api = async (method, path, body, tok) => {
  const r = await fetch(`${API}${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(tok ? { Authorization: `Bearer ${tok}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { code: r.status, json: await r.json().catch(() => null) };
};

const login = async (who, pass) => {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  const out = page.locator("button:has-text('Выйти')");
  if (await out.count()) { await out.first().click(); await page.waitForTimeout(400); await page.goto(`${BASE}/login`); }
  await page.fill("input[autocomplete=username]", who);
  await page.fill("input[autocomplete=current-password]", pass);
  await page.click("button.btn-primary");
  await page.waitForSelector("button:has-text('Выйти')", { timeout: 15000 });
};

let uid = null;
try {
  const tok = (await api("POST", "/api/auth/login", { login: "admin", password: PASS })).json.access_token;

  // ── админ: форма пользователя содержит блок «Вкладки» ──────────────────
  await login("admin", PASS);
  await page.goto(`${BASE}/admin`, { waitUntil: "networkidle" });
  await page.click('[data-tab="users"]');
  await page.waitForSelector("table, [data-users-table]", { timeout: 8000 });
  await page.click('[data-admin-action="create-user"]').catch(() => page.click("text=Новый пользователь"));
  await page.waitForSelector("[data-sections-block]", { timeout: 8000 });
  ok(await page.locator("[data-sections-block]").count() === 1, "в форме пользователя есть блок «Вкладки»");
  // галки разделов появляются, когда снят режим «без личных ограничений»
  await page.click("[data-field=sections-inherit]");
  await page.waitForTimeout(200);
  const boxes = await page.locator("[data-sections-block] input[type=checkbox][data-section]").count();
  ok(boxes === 4, `четыре галки разделов после снятия «без ограничений»: ${boxes}`);
  // последнюю галку снять нельзя — локаут запрещён и на интерфейсе
  await page.click("[data-section=statistics]");
  await page.click("[data-section=warehouses]");
  await page.click("[data-section=reports]");
  await page.click("[data-section=vessels]");
  await page.waitForTimeout(200);
  const left = await page.locator("[data-sections-block] input[data-section]:checked").count();
  ok(left === 1, `снять последнюю галку не даёт интерфейс: осталось ${left}`);
  await page.click("[data-field=sections-inherit]");
  await page.waitForTimeout(200);
  const back = await page.locator("[data-field=sections-inherit]").isChecked();
  ok(back, "вернуть режим «по роли» можно");

  // создаём пользователя только с «Индикаторами»
  const created = await api("POST", "/api/admin/users", {
    login: LOGIN, full_name: "Тест Личные Вкладки", password: USERPASS, role: "manager",
    sections: ["statistics"],
  }, tok);
  ok(created.code === 201, `создан пользователь с sections=[statistics]: ${created.code}`);
  uid = created.json?.id;

  // пустой список бэкенд обязан отклонить
  const empty = await api("POST", "/api/admin/users", {
    login: LOGIN + "x", full_name: "Тест Пусто", password: USERPASS, role: "manager", sections: [],
  }, tok);
  ok(empty.code === 400 || empty.code === 422, `пустой список вкладок отклонён: ${empty.code}`);

  // в списке — бейдж про свои вкладки
  await page.reload({ waitUntil: "networkidle" });
  await page.click('[data-tab="users"]');
  await page.waitForSelector(`text=${LOGIN}`, { timeout: 8000 });
  const row = await page.locator(`tr:has-text("${LOGIN}")`).innerText();
  ok(/Индикаторы/.test(row) && /свои вкладки/.test(row), `в списке виден бейдж «свои вкладки»: ${row.replace(/\s+/g, " ").slice(0, 90)}`);

  // ── под пользователем: одна вкладка, чужие адреса редиректят ───────────
  await login(LOGIN, USERPASS);
  const labels = await page.$$eval("nav a", (els) => els.map((e) => e.textContent.trim()));
  ok(labels.join(",") === "Индикаторы", `в шапке только «Индикаторы»: ${labels.join(", ")}`);
  for (const [path, name] of [["/reports", "Отчёты"], ["/warehouses", "Склады 1С"], ["/vessels", "Постановка"]]) {
    await page.goto(`${BASE}${path}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);
    const at = new URL(page.url()).pathname;
    ok(at === "/", `прямой заход на ${name} (${path}) → редирект на ${at}`);
  }
  const denied = await api("GET", "/api/reports/processing?days=7", null,
    (await api("POST", "/api/auth/login", { login: LOGIN, password: USERPASS })).json.access_token);
  ok(denied.code === 403, `API «Отчётов» отвечает 403, а не только прячет вкладку: ${denied.code}`);
} finally {
  if (uid) {
    const tok = (await api("POST", "/api/auth/login", { login: "admin", password: PASS })).json.access_token;
    await api("DELETE", `/api/admin/users/${uid}`, null, tok);
    console.log("ℹ️  тестовый пользователь удалён");
  }
  await browser.close();
}
console.log(problems.length ? `ПРОБЛЕМЫ: ${problems.length}` : "все проверки прошли");
process.exit(problems.length ? 1 : 0);
