/**
 * Браузерная проверка «Постановки судов»: ручной план создаётся из UI, хранится,
 * находится за любой период, не даёт поставить два судна на одну дорожку и
 * остаётся в журнале.
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> [BASE=...] [PASS=...] node tools/ui_vessels.mjs
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

const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const plus = (n) => ymd(new Date(Date.now() + n * 86400_000));

const login = async (page, who, pass, attempt = 0) => {
  // если сессия уже есть — выходим: маршрут /login у авторизованного редиректит
  // на сводку, и форма входа просто не появится
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  const out = page.locator("button:has-text('Выйти')");
  if (await out.count()) {
    await out.first().click();
    await page.waitForSelector("input[autocomplete=username]", { timeout: 8000 }).catch(() => undefined);
  }
  if (!(await page.locator("input[autocomplete=username]").count())) {
    await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  }
  await page.fill("input[autocomplete=username]", who);
  await page.fill("input[autocomplete=current-password]", pass);
  await page.click("button.btn-primary");
  const entered = await page
    .waitForSelector("[data-live]", { timeout: 15000 })
    .then(() => true)
    .catch(() => false);
  if (!entered) {
    // на локалхосте все тесты идут под одним IP, а вход троттлится 20 попытками
    // в минуту: при запуске нескольких наборов подряд попадаем в 429
    if (attempt < 3) {
      await page.waitForTimeout(15_000);   // окно троттлинга — минута
      return login(page, who, pass, attempt + 1);
    }
    throw new Error(`не удалось войти как ${who}`);
  }
};

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM, headless: true });

// API-клиент теста: подчищаем учётки прошлых прогонов, иначе проверка
// «второе судно на дорожку» будет конфликтовать с остатками предыдущего запуска
const apiTok = await fetch(`${API}/api/auth/login`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ login: "admin", password: PASS }),
})
  .then((r) => r.json())
  .then((d) => d.access_token);

const SETTINGS_KEYS = ["vessels.view_roles", "vessels.edit_roles", "vessels.delete_roles"];

const api = (path, method = "GET", body) =>
  fetch(`${API}${path}`, {
    method,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiTok}` },
    body: body ? JSON.stringify(body) : undefined,
  });

// прогон обязан быть самовосстанавливающимся: если прошлый упал посередине,
// в БД остаются его настройки (например view_roles=top) и тестовые учётки,
// и следующий стартует уже «с правами не у того» — сбрасываем до начала
await api("/api/admin/settings/reset", "POST", SETTINGS_KEYS);

async function cleanup() {
  const plan = await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json());
  for (const p of plan.placements) {
    if (/^(QA SHIP|SECOND SHIP|DBG SHIP|MANAGER TRY|PER USER RIGHT)/.test(p.vessel)) {
      await api(`/api/vessels/placements/${p.id}`, "DELETE");
    }
  }
  // тестовые причалы не удаляем (удаления в кабинете нет) — выключаем
  for (const b of await api("/api/vessels/berths").then((r) => r.json())) {
    if (b.code.startsWith("QA-") && b.is_active) {
      await api(`/api/vessels/berths/${b.id}`, "PATCH", { ...b, is_active: false });
    }
  }
}
await cleanup();

// отдельный причал для теста: он всегда свободен, и его не занимают демо-данные
const BERTH_CODE = `QA-${Date.now().toString(36)}`;
const testBerth = await api("/api/vessels/berths", "POST", { code: BERTH_CODE, length_m: 120, lanes: 2 })
  .then((r) => r.json());

const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
page.on("pageerror", (e) => problems.push(`JS: ${e.message}`));

await login(page, "admin", PASS);
await page.click("[data-nav='vessels']");
await page.waitForSelector("[data-vessels-page]");
{
  // панель периода: компактно и у левого края плашки (в шапке кнопки были крупнее
  // и уезжали в правый угол к «Выйти»)
  const geo = await page.evaluate(() => {
    const t = document.querySelector("[data-vessels-toolbar]");
    const panel = document.querySelector("[data-vessels-page]");
    const pr = panel.getBoundingClientRect();
    const kids = [...t.children].map((el) => el.getBoundingClientRect());
    return {
      left: Math.round(kids[0].left - pr.left),
      heights: kids.map((r) => Math.round(r.height)),
      // строки считаем по центрам: align-items:center даёт разный top у
      // элементов разной высоты, но строка при этом одна
      rows: new Set(kids.map((r) => Math.round(r.top + r.height / 2))).size,
      count: kids.length,
    };
  });
  ok(geo.left <= 20, `панель периода начинается у левого края плашки (${geo.left}px)`);
  ok(Math.max(...geo.heights) <= 30, `кнопки компактные: самая высокая ${Math.max(...geo.heights)}px`);
  ok(geo.rows === 1, `вся панель периода в одну строку (${geo.count} элементов, ${geo.rows} строк)`);
}
// ждём именно блоков причалов: панель появляется раньше, чем приходят данные,
// и иначе первый же подсчёт ловит пустой экран
await page.waitForSelector("[data-berth-block]", { timeout: 15000 });

// -------------------------------------------------------------- сетка ----
const blocks = await page.locator("[data-berth-block]").count();
ok(blocks >= 6, `блоков причалов в сетке: ${blocks}`);
// вертикальная шкала: 10 м на ячейку. Тестовый причал 120 м → 12 отметок
const ruler = await page.evaluate((code) => {
  const b = [...document.querySelectorAll("[data-berth-block]")].find((el) => el.dataset.berthBlock === code);
  return b ? [...b.querySelectorAll("[data-ruler]")].map((r) => Number(r.dataset.ruler)) : [];
}, BERTH_CODE);
ok(ruler.length === 12 && ruler[0] === 10 && ruler[11] === 120,
  `шкала длины: ${ruler.length} ячеек по 10 м, последняя отметка ${ruler[ruler.length - 1]} м`);
const labeled = await page.evaluate((code) => {
  const b = [...document.querySelectorAll("[data-berth-block]")].find((el) => el.dataset.berthBlock === code);
  return [...b.querySelectorAll("[data-ruler]")].filter((r) => r.textContent.trim()).length;
}, BERTH_CODE);
ok(labeled === 6, `при сжатой ячейке подписи каждые 20 м, чтобы не сливались: ${labeled} подписей`);
// ячейка сжата в полтора раза: 10 м = 12 px
const cellH = await page.evaluate(() => {
  const r = document.querySelector("[data-ruler]");
  return Math.round(r.getBoundingClientRect().height);
});
ok(cellH === 12, `ячейка шкалы по высоте ${cellH}px (10 м)`);
const days = await page.locator("[data-day]").count();
ok(days >= 30, `столбцов суток в месячном виде: ${days}`);
const scroll = await page.evaluate(() => {
  const g = document.querySelector("[data-vp-grid]");
  return { w: g.scrollWidth, c: g.clientWidth };
});
ok(scroll.w > scroll.c, `сетка листается по горизонтали (${scroll.w} > ${scroll.c})`);
const sticky = await page.evaluate(() => {
  const g = document.querySelector("[data-vp-grid]");
  g.scrollLeft = 400;
  const cell = g.querySelector("[data-berth-block] [data-ruler]");
  const r = cell.getBoundingClientRect();
  const gr = g.getBoundingClientRect();
  g.scrollLeft = 0;
  return r.left >= gr.left - 1 && r.left < gr.left + 60;
});
ok(sticky, "колонка причала остаётся липкой при прокрутке");

// ------------------------------------------------------- создание из UI ----
const VESSEL = `QA SHIP ${Date.now().toString(36)}`;
const D1 = plus(3);
const D2 = plus(5);

await page.click("[data-admin-action='new-placement']");
await page.waitForSelector("[data-modal='placement-form']");
await page.selectOption('[data-field="berth"]', String(testBerth.id));
await page.fill('[data-field="vessel"]', VESSEL);
await page.fill('[data-field="length-m"]', "60");
await page.fill('[data-field="offset-m"]', "0");
await page.fill('[data-field="volume"]', "5 500");
await page.fill('[data-field="cargo"]', "БШ");
await page.fill('[data-field="note"]', "(КМП-ЮМФ) сборник");
await page.fill('[data-field="start-date"]', D1);
await page.selectOption('[data-field="start-hour"]', "6");
await page.fill('[data-field="end-date"]', D2);
await page.selectOption('[data-field="end-hour"]', "12");
await page.click("[data-admin-action='save-placement']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached", timeout: 8000 });

const bar = page.locator(`[data-vp-bar]`).filter({ hasText: VESSEL.split(" ")[0] });
await page.waitForSelector(`[data-vp-bar]`, { timeout: 8000 });
const barCount = await page.locator(`[data-vp-bar]`).filter({ hasText: VESSEL }).count();
ok(barCount === 1, `полоса «${VESSEL}» появилась в сетке`);

const geo = await page.evaluate((name) => {
  const bars = [...document.querySelectorAll("[data-vp-bar]")];
  const el = bars.find((b) => b.textContent.includes(name));
  const r = el.getBoundingClientRect();
  return { w: Math.round(r.width), h: Math.round(r.height), text: el.textContent.trim() };
}, VESSEL);
ok(geo.w > 60 && geo.h >= 20, `полоса растянутая: ${geo.w}×${geo.h}px, «${geo.text}»`);
// 60 м при 12 px на 10 м → 72 px минус зазор 2
ok(Math.abs(geo.h - 70) <= 3, `высота полосы соответствует длине: ${geo.h}px ≈ 60 м`);
const barMeters = await page.evaluate((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  return { length: el?.dataset.lengthM, offset: el?.dataset.offsetM };
}, VESSEL);
ok(barMeters.length === "60" && barMeters.offset === "0",
  `в сетке видно длину и место: ${barMeters.offset}+${barMeters.length} м`);

// --------------------------------------------------------- конфликт ----
await page.click("[data-admin-action='new-placement']");
await page.waitForSelector("[data-modal='placement-form']");
await page.selectOption('[data-field="berth"]', String(testBerth.id));
await page.fill('[data-field="vessel"]', "SECOND SHIP");
await page.fill('[data-field="length-m"]', "60");
await page.fill('[data-field="offset-m"]', "30");
await page.fill('[data-field="start-date"]', plus(4));
await page.fill('[data-field="end-date"]', plus(4));
await page.click("[data-admin-action='save-placement']");
await page.waitForSelector("[data-modal='placement-form'] [data-form-error]", { timeout: 8000 });
const err = (await page.textContent("[data-modal='placement-form'] [data-form-error]")) || "";
ok(err.includes(VESSEL.split(" ")[0]) && err.includes(BERTH_CODE) && err.includes("30–60 м"),
   `судно наехавшее по длине не пустили: «${err}»`);

// то же время, но свободные метры — ставить можно
await page.fill('[data-field="vessel"]', "SECOND SHIP");
await page.fill('[data-field="length-m"]', "50");
await page.fill('[data-field="offset-m"]', "70");
await page.click("[data-admin-action='save-placement']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached", timeout: 8000 });
ok((await page.locator("[data-vp-bar]").filter({ hasText: "SECOND SHIP" }).count()) === 1,
   "второе судно в то же время встало, если метры не пересекаются");

// подсказка свободного места: не заставляем гадать и ловить 409
await page.click("[data-admin-action='new-placement']");
await page.waitForSelector("[data-modal='placement-form']");
await page.selectOption('[data-field="berth"]', String(testBerth.id));
await page.fill('[data-field="start-date"]', plus(4));
await page.fill('[data-field="end-date"]', plus(4));
await page.fill('[data-field="length-m"]', "10");
await page.click("[data-admin-action='find-free']");
await page.waitForSelector("[data-free-hint]", { timeout: 8000 });
const freeOffset = await page.inputValue('[data-field="offset-m"]');
ok(freeOffset === "60", `подсказка свободного места: ${await page.textContent("[data-free-hint]")}`);
await page.click("[data-modal='placement-form'] [data-admin-action='close-modal']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached" });

// судно длиннее причала — не проходим (тестовый причал 120 м)
await page.click("[data-admin-action='new-placement']");
await page.waitForSelector("[data-modal='placement-form']");
await page.selectOption('[data-field="berth"]', String(testBerth.id));
await page.fill('[data-field="vessel"]', "TOO LONG SHIP");
await page.fill('[data-field="length-m"]', "140");
await page.fill('[data-field="start-date"]', plus(20));
await page.fill('[data-field="end-date"]', plus(21));
await page.click("[data-admin-action='save-placement']");
await page.waitForSelector("[data-modal='placement-form'] [data-form-error]", { timeout: 8000 });
const fitErr = (await page.textContent("[data-modal='placement-form'] [data-form-error]")) || "";
ok(fitErr.includes("длина причала 120 м"), `судно длиннее причала не приняли: «${fitErr}»`);
await page.click("[data-modal='placement-form'] [data-admin-action='close-modal']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached" });

// ───────────────────────────── WYSIWYG: перетаскивание мышью ─────────────────────────
const pencil = page.locator("[data-admin-action='wysiwyg']");
ok((await pencil.count()) === 1, "карандаш режима перетаскивания есть");
await pencil.click();
await page.waitForSelector("[data-wysiwyg-hint]");
ok(true, "карандаш включил режим: подсказка на месте");

const barSel = `[data-vp-bar]`;
const before = await page.evaluate((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  const r = el.getBoundingClientRect();
  return { offset: el.dataset.offsetM, left: Math.round(r.left), top: Math.round(r.top) };
}, VESSEL);
const box = await page.locator(`[data-vp-bar][data-offset-m="${before.offset}"]`)
  .filter({ hasText: VESSEL }).first().boundingBox();
await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
await page.mouse.down();
await page.mouse.move(box.x + box.width / 2 + 33, box.y + box.height / 2 + 12, { steps: 6 });
await page.mouse.up();
await page.waitForFunction((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  return el?.dataset.offsetM === "10";
}, VESSEL, { timeout: 8000 });
const after = await page.evaluate((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  const r = el.getBoundingClientRect();
  return { offset: el.dataset.offsetM, left: Math.round(r.left) };
}, VESSEL);
ok(after.offset === "10", `мышью передвинули место у причала: ${before.offset} → ${after.offset} м`);
ok(Math.abs(after.left - before.left - 33) <= 2,
   `мышью передвинули по времени: +1 слот (${after.left - before.left}px)`);
ok((await page.locator("[data-modal='placement-detail']").count()) === 0,
   "после перетаскивания карточка не открылась (клик не подменился)");

// наезжаем на другое судно — бэкенд не даёт, и полоса возвращается на место
const box2 = await page.locator(`[data-vp-bar][data-offset-m="10"]`).filter({ hasText: VESSEL })
  .first().boundingBox();
await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2);
await page.mouse.down();
await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2 + 24, { steps: 6 });
await page.mouse.up();
await page.waitForFunction(() => /пересечение по длине/.test(document.querySelector("[role=alert]")?.textContent ?? ""), { timeout: 8000 });
const dragErr = (await page.textContent("[role=alert]")) || "";
ok(dragErr.includes("пересечение по длине"), `съехало на другое судно — не пустило: «${dragErr}»`);
await page.waitForFunction((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  return el?.dataset.offsetM === "10";
}, VESSEL, { timeout: 8000 });
ok(true, "после отказа полоса вернулась на свою отметку");

// карандаш выключен — полоса больше не ручка
await pencil.click();
ok((await page.locator("[data-wysiwyg-hint]").count()) === 0, "карандаш выключен — подсказка ушла");
const box3 = await page.locator(`[data-vp-bar][data-offset-m="10"]`).filter({ hasText: VESSEL })
  .first().boundingBox();
await page.mouse.move(box3.x + box3.width / 2, box3.y + box3.height / 2);
await page.mouse.down();
await page.mouse.move(box3.x + box3.width / 2, box3.y + box3.height / 2 + 24, { steps: 5 });
await page.mouse.up();
await page.waitForTimeout(700);
const stillThere = await page.evaluate((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  return el?.dataset.offsetM;
}, VESSEL);
ok(stillThere === "10", "без карандаша перетаскивание не работает");
await page.click("[role=alert] ~ * [data-admin-action], body", { position: { x: 1, y: 1 } }).catch(() => {});

// ------------------------------------------------------ любой период ----
await page.fill("[data-period-from]", plus(60));
await page.fill("[data-period-to]", plus(90));
await page.waitForTimeout(900);
ok((await page.locator(`[data-vp-bar]`).filter({ hasText: VESSEL }).count()) === 0,
   "в период через два месяца постановки нет");

await page.fill("[data-period-from]", plus(4));
await page.fill("[data-period-to]", plus(10));
await page.waitForTimeout(900);
const clipped = await page.evaluate((name) => {
  const bars = [...document.querySelectorAll("[data-vp-bar]")];
  const el = bars.find((b) => b.textContent.includes(name));
  if (!el) return null;
  const cs = getComputedStyle(el);
  const grid = document.querySelector("[data-vp-grid]").getBoundingClientRect();
  const r = el.getBoundingClientRect();
  return { dotted: cs.borderLeftStyle, inside: r.left >= grid.left };
}, VESSEL);
ok(!!clipped && clipped.dotted === "dotted",
   `постановка, начатая до периода, видна обрезанной (пунктирный край: ${clipped?.dotted})`);

await page.fill("[data-period-from]", plus(0));
await page.fill("[data-period-to]", plus(30));
await page.waitForTimeout(900);

// --------------------------------------------------- детали и правка ----
await page.locator(`[data-vp-bar]`).filter({ hasText: VESSEL }).first().click();
await page.waitForSelector("[data-modal='placement-detail']");
const note = await page.textContent("[data-detail-note]");
ok((note || "").includes("сборник"), `в деталях видно примечание: «${note}»`);
await page.click("[data-admin-action='edit-placement']");
await page.waitForSelector("[data-modal='placement-form']");
await page.fill('[data-field="cargo"]', "БАНАНЫ-ПАЛЛЕТ");
await page.click("[data-admin-action='save-placement']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached", timeout: 8000 });
// сетка перезагружается после сохранения: читать надо дождавшись, иначе ловим
// старый бар и «правка не применилась» на ровном месте
await page.waitForFunction((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  return el?.getAttribute("data-cargo") === "БАНАНЫ-ПАЛЛЕТ";
}, VESSEL, { timeout: 8000 });
const cargoNow = await page.evaluate((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  return el?.getAttribute("data-cargo");
}, VESSEL);
ok(cargoNow === "БАНАНЫ-ПАЛЛЕТ", `правка применилась: груз ${cargoNow}`);

// --------------------------------------------------------- права менеджера ----
await login(page, "petrov", "demo1234");
await page.click("[data-nav='vessels']");
await page.waitForSelector("[data-vessels-page]");
ok((await page.locator("[data-admin-action='new-placement']").count()) === 0,
   "менеджер не видит кнопку «Новая постановка»");
ok((await page.locator("[data-vp-bar]").count()) > 0, "менеджер план видит");
const hint = (await page.textContent("[data-vessels-page]")) || "";
ok(hint.includes("ставят и правят") && hint.includes("топ-менеджмент"),
   `менеджеру сказано, кто ведёт план: «${(hint.match(/План можно смотреть[^]*?\./) || ["—"])[0]}»`);

// ------------------------------------------- права берутся из настроек админки ----
try {
  await api("/api/admin/settings", "PUT", { "vessels.edit_roles": "manager" });
  await login(page, "petrov", "demo1234");
  await page.click("[data-nav='vessels']");
  await page.waitForSelector("[data-vessels-page]");
  ok((await page.locator("[data-admin-action='new-placement']").count()) === 1,
     "vessels.edit_roles=manager: менеджеру выросла кнопка «Новая постановка»");

  const MV = `QA SHIP MGR ${Date.now().toString(36)}`;
  await page.click("[data-admin-action='new-placement']");
  await page.waitForSelector("[data-modal='placement-form']");
  await page.selectOption('[data-field="berth"]', String(testBerth.id));
  await page.fill('[data-field="vessel"]', MV);
  await page.fill('[data-field="start-date"]', plus(21));
  await page.fill('[data-field="end-date"]', plus(23));
  await page.click("[data-admin-action='save-placement']");
  await page.waitForSelector("[data-modal='placement-form']", { state: "detached", timeout: 8000 });
  ok((await page.locator("[data-vp-bar]").filter({ hasText: MV }).count()) === 1,
     "менеджер по настройке реально поставил судно");

  // закрываем просмотр совсем: вкладка обязана исчезнуть, а маршрут — редиректить
  await api("/api/admin/settings", "PUT", { "vessels.view_roles": "admin,top" });
  await login(page, "petrov", "demo1234");
  ok((await page.locator("[data-nav='vessels']").count()) === 0,
     "vessels.view_roles=admin,top: вкладки «Постановка» у менеджера нет");
  await page.goto(`${BASE}/vessels`, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  ok(page.url().endsWith("/") && (await page.locator("[data-vessels-page]").count()) === 0,
     "прямая ссылка /vessels у закрытой роли ведёт на сводку");

  // Права приходят со входа вместе с пользователем, поэтому запрещённая вкладка
  // не мигает, пока /api/settings/public в пути. Проверка с намеренно тормозящими
  // настройками: иначе «скрыто» было бы видно только на втором кадре.
  let slow = true;
  const slowSettings = async (route) => {
    if (slow) await new Promise((r) => setTimeout(r, 2500));
    await route.continue().catch(() => undefined);
  };
  await page.route("**/api/settings/public", slowSettings);
  await login(page, "petrov", "demo1234");
  ok((await page.locator("[data-nav='vessels']").count()) === 0,
     "вкладки нет сразу, ещё до ответа настроек");
  await api("/api/admin/settings", "PUT", { "vessels.view_roles": "admin,top,manager" });
  await login(page, "petrov", "demo1234");
  ok((await page.locator("[data-nav='vessels']").count()) === 1,
     "но и не прячемся в пустоту: с правом вкладка на месте при тех же тормозящих настройках");
  slow = false;
  await page.unroute("**/api/settings/public", slowSettings);
} finally {
  await api("/api/admin/settings/reset", "POST", SETTINGS_KEYS);
}

// ------------------------------------------------------------- удаление ----
await login(page, "admin", PASS);
await page.click("[data-nav='vessels']");
await page.waitForSelector("[data-vessels-page]");
await page.locator(`[data-vp-bar]`).filter({ hasText: VESSEL }).first().click();
await page.waitForSelector("[data-modal='placement-detail']");
await page.click("[data-admin-action='delete-placement']");
await page.waitForSelector("[data-modal='placement-detail']", { state: "detached", timeout: 8000 });
ok((await page.locator(`[data-vp-bar]`).filter({ hasText: VESSEL }).count()) === 0, "постановка удалена");


// ── право по конкретному человеку, а не по роли (как доступ к складам) ──
// стартуем честно: снимаем право, если его оставил прошлый незавершённый прогон
const users0 = await api("/api/admin/users").then((r) => r.json());
const sidorovaId = users0.find((u) => u.login === "sidorova").id;
await api(`/api/admin/users/${sidorovaId}`, "PATCH", { vessel_right: null });

await page.click("[data-nav='admin']");
await page.waitForSelector("tr[data-user='sidorova']", { timeout: 8000 });
await page.locator("tr[data-user='sidorova'] [data-admin-action='edit-user']").click();
await page.waitForSelector('[data-modal="user-edit"] select[data-field="vessel-right"]');
ok((await page.locator('select[data-field="vessel-right"]').inputValue()) === "",
  "в карточке менеджера право стоит «по роли»");
await page.locator('select[data-field="vessel-right"]').selectOption("edit");
await page.click('[data-modal="user-edit"] [data-admin-action="save-user"]');
await page.waitForSelector('[data-modal="user-edit"]', { state: "detached", timeout: 8000 });
await page.waitForFunction(() =>
  document.querySelector("tr[data-user='sidorova'] td[data-vessel-right]")?.dataset.vesselRight === "edit",
  null, { timeout: 8000 });
ok((await page.locator("tr[data-user='sidorova'] td[data-vessel-right]").textContent()) === "править",
  "в списке право видно прямо в строке пользователя");

await login(page, "sidorova", "demo1234");
await page.click("[data-nav='vessels']");
await page.waitForSelector("[data-vessels-page]");
ok((await page.locator("[data-admin-action='new-placement']").count()) === 1,
  "менеджеру выдали «ставить и править» — кнопка появилась без смены роли");
const berths = await api("/api/vessels/berths").then((r) => r.json());
const berthId = berths.find((b) => b.code === "РП-4")?.id;
const byRight = await api("/api/vessels/placements", "POST", {
  berth_id: berthId, lane: 2, vessel: "PER USER RIGHT", cargo: "Кофе", volume: "300 т",
  starts_at: "2028-02-01T00:00:00", ends_at: "2028-02-02T00:00:00",
});
ok(byRight.status === 201, `менеджер ставит судно по личному праву (${byRight.status})`);
if (byRight.status === 201) await api(`/api/vessels/placements/${(await byRight.json()).id}`, "DELETE");

await login(page, "admin", PASS);
await page.click("[data-nav='admin']");
await page.waitForSelector("tr[data-user='sidorova']", { timeout: 8000 });
await page.locator("tr[data-user='sidorova'] [data-admin-action='edit-user']").click();
await page.waitForSelector('[data-modal="user-edit"] select[data-field="vessel-right"]');
await page.locator('select[data-field="vessel-right"]').selectOption("");
await page.click('[data-modal="user-edit"] [data-admin-action="save-user"]');
await page.waitForSelector('[data-modal="user-edit"]', { state: "detached", timeout: 8000 });
await page.waitForFunction(() =>
  document.querySelector("tr[data-user='sidorova'] td[data-vessel-right]")?.dataset.vesselRight === "role",
  null, { timeout: 8000 });
ok(true, "право сняли — снова «по роли»");

await page.screenshot({ path: "shot_vessels.png", fullPage: false });
await cleanup();
await browser.close();

console.log(problems.length ? `\nПРОБЛЕМЫ:\n${problems.join("\n")}` : "\nвсе проверки прошли");
process.exit(problems.length ? 1 : 0);
