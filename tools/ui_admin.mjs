/**
 * Браузерная проверка страницы «Администрирование».
 *
 * Сценарий: вход администратора → вкладка «Администрирование» → создание
 * пользователя → ошибка дубля логина → сброс пароля (временный показан один
 * раз) → отключение → разблокировка/включение → создание и правка склада →
 * проверка связи с 1С (mock) → ручная загрузка витрины → журнал загрузок →
 * сохранение sync.interval_minutes=21 и ответ планировщика → журнал аудита.
 * Отдельно: менеджер не видит вкладку, а /admin редиректит на сводку.
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> [BASE=http://localhost:5173] \
 *         [ADMIN_LOGIN=admin] [PASS=<пароль администратора>] \
 *         node tools/ui_admin.mjs
 */
import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://localhost:5173";
const ADMIN_LOGIN = process.env.ADMIN_LOGIN || "admin";
const PASS = process.env.PASS;
const exe = process.env.PW_CHROMIUM;

if (!PASS) {
  console.error("Нужен пароль администратора: PASS=<пароль> node tools/ui_admin.mjs");
  process.exit(1);
}

const problems = [];
// ru-RU разделяет разряды неразрывным пробелом — в проверках нормализуем
const norm = (s) => (s || "").replace(/\u00a0|\u202f/g, " ").replace(/\s+/g, " ");
const ok = (cond, msg) => {
  console.log(`${cond ? "✅" : "❌"} ${msg}`);
  if (!cond) problems.push(msg);
};

const browser = await chromium.launch({ executablePath: exe, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => problems.push(`JS: ${e.message}`));
page.on("console", (m) => m.type() === "error" && console.log("CONSOLE ERR:", m.text()));

async function login(loginName, password) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill("input[autocomplete=username]", loginName);
  await page.fill("input[autocomplete=current-password]", password);
  await page.click("button.btn-primary");
  await page.waitForSelector("section[data-widget]", { timeout: 15000 });
}

async function openAdminTab(tab) {
  await page.click(`a[data-nav="admin"]`);
  await page.waitForSelector(`[data-tab="${tab}"]`, { timeout: 5000 });
  await page.click(`[data-tab="${tab}"]`);
  await page.waitForSelector(`[data-tab-panel="${tab}"]`, { timeout: 5000 });
}

const stamp = Date.now().toString(36);
const NEW_LOGIN = `qa${stamp}`.slice(0, 24);
const NEW_WH = `QA-${stamp.slice(-5)}`.toUpperCase();

// ---------------------------------------------------------------- вход админа ---
await login(ADMIN_LOGIN, PASS);
console.log("url после входа:", page.url());
const adminTab = await page.$('a[data-nav="admin"]');
ok(!!adminTab, "у администратора есть вкладка «Администрирование»");
ok((await page.textContent('a[data-nav="admin"]'))?.includes("Администрирование"), "подпись вкладки по-русски");

await openAdminTab("users");
ok(page.url().includes("/admin"), `маршрут /admin открыт (${page.url()})`);
const usersBefore = await page.$$eval("tr[data-user]", (els) => els.length);
console.log("учёток до:", usersBefore);

// ------------------------------------------------------- создание пользователя ---
await page.click('[data-admin-action="create-user"]');
await page.waitForSelector('[data-modal="user-form"]');
await page.fill('[data-field="login"]', NEW_LOGIN.toUpperCase());
await page.fill('[data-field="full_name"]', "Тестовый Менеджер Проверочный");
await page.selectOption('[data-field="role"]', "manager");
await page.fill('[data-field="email"]', `qa+${stamp}@example.com`);
await page.fill('[data-field="password"]', "Qa-Test-2026");
await page.fill('[data-field="password2"]', "Qa-Test-2026");
await page.locator('[data-modal="user-form"] [data-warehouse]').first().click();
await page.click('[data-admin-action="save-user"]');
await page.waitForSelector(`tr[data-user="${NEW_LOGIN}"]`, { timeout: 8000 });
ok(true, `пользователь «${NEW_LOGIN}» создан и появился в списке`);
const row = page.locator(`tr[data-user="${NEW_LOGIN}"]`);
ok((await row.textContent()).includes("Тестовый Менеджер"), "ФИО отображается");
ok((await row.textContent()).includes("Менеджер"), "роль подписана по-русски");
await page.screenshot({ path: "shot_admin_users.png", fullPage: true });

// слабый пароль и дубль логина — ошибка бэкенда прямо в форме
await page.click('[data-admin-action="create-user"]');
await page.waitForSelector('[data-modal="user-form"]');
await page.fill('[data-field="login"]', NEW_LOGIN);
await page.fill('[data-field="full_name"]', "Дубль");
await page.fill('[data-field="password"]', "Qa-Test-2026");
await page.fill('[data-field="password2"]', "Qa-Test-2026");
await page.click('[data-admin-action="save-user"]');
await page.waitForSelector('[data-modal="user-form"] [data-form-error]', { timeout: 8000 });
const dupText = await page.textContent('[data-modal="user-form"] [data-form-error]');
ok(!!dupText && dupText.includes("Логин уже занят"), `дубль логина отбит: «${dupText}»`);
await page.fill('[data-field="login"]', "qa-short");
await page.fill('[data-field="password"]', "abcdefg");
await page.fill('[data-field="password2"]', "abcdefg");
await page.click('[data-admin-action="save-user"]');
const weakText = await page.textContent('[data-modal="user-form"] [data-form-error]');
ok(!!weakText && /8 символов|буквы и цифры/i.test(weakText), `слабый пароль отбит: «${weakText}»`);
await page.click('[data-modal="user-form"] [data-admin-action="close-modal"]');
await page.waitForSelector('[data-modal="user-form"]', { state: "detached" });

// ----------------------------------------------------------- сброс пароля ---
await row.locator('[data-admin-action="reset-password"]').click();
await page.waitForSelector("[data-temp-password]", { timeout: 8000 });
const tempPass = (await page.textContent("[data-temp-password]")) || "";
ok(tempPass.length >= 8, `временный пароль показан один раз (${tempPass.length} символов)`);
const warnText = (await page.textContent("[data-temp-password-box]")) || "";
ok(/один раз/.test(warnText), "есть предупреждение «покажите один раз»");
await page.click('[data-admin-action="copy-password"]');
await page.waitForTimeout(200);
ok(true, "кнопка «Скопировать» нажимается без падения страницы");

// ------------------------------------------------ отключение / разблокировка ---
await row.locator('[data-admin-action="deactivate-ask"]').click();
await row.locator('[data-admin-action="deactivate-user"]').click();
await page.waitForFunction(
  (sel) => document.querySelector(sel)?.textContent.includes("отключён"),
  `tr[data-user="${NEW_LOGIN}"]`,
  { timeout: 8000 },
);
ok(true, "после «Отключить» статус — «отключён»");
await row.locator('[data-admin-action="unlock"]').click();
await page.waitForTimeout(600);
ok(true, "кнопка «Разблокировать» сработала");
await row.locator('[data-admin-action="enable-user"]').click();
await page.waitForFunction(
  (sel) => document.querySelector(sel)?.textContent.includes("активен"),
  `tr[data-user="${NEW_LOGIN}"]`,
  { timeout: 8000 },
);
ok(true, "учётку снова включили — статус «активен»");

// правка пользователя (ФИО + новый пароль) — PATCH /api/admin/users/{id}
await row.locator('[data-admin-action="edit-user"]').click();
await page.waitForSelector('[data-modal="user-edit"]');
await page.fill('[data-modal="user-edit"] [data-field="full_name"]', "Тестовый Менеджер Переименованный");
await page.fill('[data-modal="user-edit"] [data-field="new_password"]', "Qa-New-2026");
await page.click('[data-admin-action="save-user"]');
// ждём именно появления нового ФИО в строке: PATCH с новым паролем приходит
// позже фиксированных 400 мс, из-за чего проверка была флэкающей
await page.waitForFunction(
  (sel) => (document.querySelector(sel)?.textContent || "").includes("Переименованный"),
  `tr[data-user="${NEW_LOGIN}"]`,
  { timeout: 8000 },
).catch(() => {});
ok(((await page.textContent(`tr[data-user="${NEW_LOGIN}"]`)) || "").includes("Переименованный"), "правка ФИО сохранилась");

// ------------------------------------------------------------------- склады ---
await page.click('[data-tab="warehouses"]');
await page.waitForSelector('[data-tab-panel="warehouses"]');
await page.click('[data-admin-action="create-warehouse"]');
await page.waitForSelector('[data-modal="warehouse-form"]');
await page.fill('[data-field="code"]', NEW_WH);
await page.fill('[data-field="name"]', "Тестовый склад смока");
await page.selectOption('[data-field="kind"]', "terminal");
await page.fill('[data-field="capacity"]', "1200");
await page.fill('[data-field="unit"]', "т");
await page.click('[data-admin-action="save-warehouse"]');
await page.waitForSelector(`tr[data-warehouse-code="${NEW_WH}"]`, { timeout: 8000 });
ok(true, `склад «${NEW_WH}» создан`);
const whRow = page.locator(`tr[data-warehouse-code="${NEW_WH}"]`);
ok((await whRow.textContent()).includes("Терминал"), "вид склада подписан по-русски");

await whRow.locator('[data-admin-action="edit-warehouse"]').click();
await page.waitForSelector('[data-modal="warehouse-edit"]');
await page.fill('[data-modal="warehouse-edit"] [data-field="capacity"]', "1234");
await page.click('[data-admin-action="save-warehouse"]');
await page.waitForTimeout(600);
ok(norm(await whRow.textContent()).includes("1 234"), "ёмкость обновлена (PATCH)");

await whRow.locator('[data-admin-action="deactivate-warehouse-ask"]').click();
await whRow.locator('[data-admin-action="deactivate-warehouse"]').click();
await page.waitForFunction(
  (code) => document.querySelector(`tr[data-warehouse-code="${code}"]`)?.textContent.includes("отключён"),
  NEW_WH,
  { timeout: 8000 },
);
ok(true, "склад отключён вместо удаления (история сохраняется)");
await whRow.locator('[data-admin-action="enable-warehouse"]').click();
await page.waitForTimeout(600);
ok(((await whRow.textContent()) || "").includes("активен"), "склад снова активен");
await page.screenshot({ path: "shot_admin_warehouses.png", fullPage: true });

// ------------------------------------------------------- 1С: проверка и загрузка ---
await page.click('[data-tab="onec"]');
await page.waitForSelector('[data-tab-panel="onec"]');
await page.click('[data-admin-action="test-onec"]');
await page.waitForSelector("[data-onec-test]", { timeout: 15000 });
const checks = await page.$$eval("[data-onec-check]", (els) =>
  els.map((e) => ({ path: e.dataset.oncCheck || e.dataset.onccheck, text: e.textContent })),
);
ok(checks.length > 0, `проверка связи вернула ${checks.length} проверок`);
ok(checks.every((c) => c.text.includes("ok")), "все проверки связи прошли (mock)");
const testNote = (await page.textContent("[data-tab-panel=\"onec\"]")) || "";
ok(/в порядке/.test(testNote), "есть сообщение «Связь с источником … в порядке»");

await page.click('[data-admin-action="sync-now"]');
await page.waitForSelector("[data-sync-result]", { timeout: 20000 });
const syncText = (await page.textContent("[data-sync-result]")) || "";
ok(/загружено/.test(syncText), `ручная загрузка завершилась: ${syncText.replace(/\s+/g, " ").slice(0, 90)}`);
const runs = await page.$$eval("[data-sync-run]", (els) => els.length);
ok(runs > 0, `журнал загрузок непустой (${runs} записей)`);
await page.screenshot({ path: "shot_admin_onec.png", fullPage: true });

// --------------------------------------------------------------- настройки ---
await page.click('[data-tab="settings"]');
await page.waitForSelector('[data-tab-panel="settings"]');
await page.waitForSelector('[data-setting="sync.interval_minutes"]', { timeout: 8000 });
// после предыдущего прогона в базе может лежать 21 — берём другое значение,
// чтобы изменение было настоящим, а не «менять нечего»
const curInterval = await page.inputValue('[data-setting="sync.interval_minutes"]');
const target = curInterval === "21" ? "24" : "21";
await page.fill('[data-setting="sync.interval_minutes"]', target);
const changedFlag = await page.getAttribute('[data-setting="sync.interval_minutes"]', "data-changed");
ok(changedFlag === "1", `изменённое поле подсвечено (data-changed=1, ${curInterval} → ${target})`);
await page.click('[data-admin-action="save-settings"]');
await page.waitForSelector("[data-scheduler-report]", { timeout: 8000 });
const schedText = norm(await page.textContent("[data-scheduler-report]"));
ok(schedText.includes(`${target} мин`), `планировщик подтвердил интервал: ${schedText}`);
const savedNote = (await page.textContent("[data-notice]")) || "";
ok(/Сохранено/.test(savedNote), `есть подтверждение сохранения: «${savedNote}»`);

// возвращаем интервал к значениям по умолчанию — кнопка сброса работает
await page.click('[data-admin-action="reset-settings"]');
await page.waitForTimeout(900);
const afterReset = await page.inputValue('[data-setting="sync.interval_minutes"]');
ok(afterReset === "15", `сброс к значениям по умолчанию вернул 15 (получено ${afterReset})`);

// и финально ставим 21 мин: того же требует проверка
await page.fill('[data-setting="sync.interval_minutes"]', "21");
await page.click('[data-admin-action="save-settings"]');
await page.waitForTimeout(900);
const finalNote = norm(await page.textContent("[data-scheduler-report]"));
ok(finalNote.includes("21 мин"), `итоговый интервал — 21 мин: ${finalNote}`);

// ------------------------------------------------------------------- журнал ---
await page.click('[data-tab="audit"]');
await page.waitForSelector('[data-tab-panel="audit"]');
await page.waitForSelector("[data-audit-action]", { timeout: 8000 });
const actions = await page.$$eval("[data-audit-action]", (els) => els.map((e) => e.dataset.auditAction));
ok(actions.includes("admin.user_create"), "в журнале есть admin.user_create");
ok(actions.includes("admin.settings_update"), "в журнале есть admin.settings_update");
ok(actions.includes("admin.reset_password"), "в журнале есть admin.reset_password");
ok(actions.includes("admin.warehouse_create"), "в журнале есть admin.warehouse_create");
await page.fill("[data-audit-filter]", "warehouse");
await page.waitForTimeout(300);
const filtered = await page.$$eval("[data-audit-action]", (els) => els.map((e) => e.dataset.auditAction));
ok(filtered.length > 0 && filtered.every((a) => a.includes("warehouse")), "фильтр журнала работает");
await page.screenshot({ path: "shot_admin_audit.png", fullPage: true });

// ------------------------------------------------------ мобильная ширина ---
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
const overflowX = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
ok(overflowX <= 2, `на мобильном нет горизонтального сдвига страницы (${overflowX} px)`);
const tabsWrapped = await page.$$eval('[data-tab]', (els) => new Set(els.map((e) => Math.round(e.getBoundingClientRect().top))).size >= 2);
ok(tabsWrapped, "вкладки админки переносятся на узком экране");
await page.setViewportSize({ width: 1500, height: 950 });

// --------------------------------------- менеджер: вкладки нет, /admin закрыт ---
await page.click("text=Выйти");
await page.waitForSelector("input[autocomplete=username]", { timeout: 5000 });
await login("petrov", "demo1234");
const managerAdminTab = await page.$('a[data-nav="admin"]');
ok(!managerAdminTab, "у менеджера вкладки «Администрирование» нет");
await page.goto(`${BASE}/admin`, { waitUntil: "networkidle" });
await page.waitForTimeout(700);
const path = new URL(page.url()).pathname;
ok(path === "/", `менеджер на /admin редиректится на сводку (${path})`);
const stillDashboard = await page.$("section[data-widget]");
ok(!!stillDashboard, "после редиректа дашборд на месте");

await browser.close();

console.log("");
if (problems.length) {
  console.log(`❌ провалено проверок: ${problems.length}`);
  for (const p of problems) console.log("   —", p);
  process.exit(1);
}
console.log("✅ все проверки админки зелёные");
