/**
 * Браузерная проверка «Постановки судов»: ручной план создаётся из UI, хранится,
 * находится за любой период, не даёт поставить два судна на одну дорожку и
 * остаётся в журнале.
 *
 * Запуск: PW_CHROMIUM=<путь к chrome> [BASE=...] [PASS=...] node tools/ui_vessels.mjs
 */
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

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
  // «живой» индикатор в шапке живёт только на витрине складов, поэтому ждём
  // универсального признака авторизованного кабинета — кнопки «Выйти».
  const entered = await page
    .waitForSelector("button:has-text('Выйти')", { timeout: 15000 })
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

// Следы прогонов и разовых замеров: эти причалы не принадлежат терминалу.
const JUNK_BERTH = /^(Q[AB]-|MV-|LF-|RZ-|CAS-)/;

async function cleanup() {
  const plan = await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json());
  const junk = new Set(plan.berths.filter((b) => JUNK_BERTH.test(b.code)).map((b) => b.id));
  for (const p of plan.placements) {
    if (/^(QA SHIP|SECOND SHIP|DBG SHIP|MANAGER TRY|PER USER RIGHT)/.test(p.vessel) || junk.has(p.berth_id)) {
      await api(`/api/vessels/placements/${p.id}`, "DELETE");
    }
  }
  // тестовые причалы удаляем совсем: выключенный причал остаётся в справочнике,
  // и после десятка прогонов администратор видит в плане мусор вместо причалов
  for (const b of await api("/api/vessels/berths").then((r) => r.json())) {
    if (!JUNK_BERTH.test(b.code)) continue;
    const r = await api(`/api/vessels/berths/${b.id}`, "DELETE");
    // 409 = на причал вышел чужой корпус. Молча оставлять его нельзя: упавший
    // прогон оставил так причал с sort_order 0, который встал между РП-1 и РП-2 и
    // принял на себя нос «МАРИНЫ», а красная проверка стрелок искали потом в коде
    if (r.status === 409) {
      console.log(`⚠️ причал ${b.code} держит чужой нахлёст — удалите его вручную: ${String((await r.json().catch(() => ({})))?.detail || "").slice(0, 90)}`);
    }
  }
}
await cleanup();

// отдельный причал для теста: он всегда свободен, и его не занимают демо-данные
const BERTH_CODE = `QA-${Date.now().toString(36)}`;
const BERTH2_CODE = `QB-${Date.now().toString(36)}`;
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
  // мерить надо саму липкую колонку ([data-berth-col]), а не подпись причала и не
  // отметку шкалы: подпись центрируется по высоте и зависит от метража, а отметка
  // шкалы лежит в правой части колонки, за разделительной чертой (её left —
  // LEFT_W - RULER_W, и порог в 60 px она не проходит при любом scrollLeft)
  const cell = g.querySelector("[data-berth-block] [data-berth-col]");
  const r = cell.getBoundingClientRect();
  const gr = g.getBoundingClientRect();
  g.scrollLeft = 0;
  return r.left >= gr.left - 1 && r.left < gr.left + 60;
});
ok(sticky, "колонка причала остаётся липкой при прокрутке");
const split = await page.evaluate(() => {
  const col = document.querySelector("[data-berth-block] [data-berth-col]");
  const marks = col.querySelectorAll("[data-ruler]");
  const last = marks[marks.length - 1];
  const colRight = col.getBoundingClientRect().right;
  const markRight = last.getBoundingClientRect().right;
  const markLeft = last.getBoundingClientRect().left;
  return {
    rulerGlued: Math.abs(colRight - markRight) < 2,
    rulerNarrow: markLeft - col.getBoundingClientRect().left > 40,
  };
});
ok(split.rulerGlued && split.rulerNarrow, "линейка метров — отдельная узкая полоса у правого края колонки, отделённая от подписи причала");


// ─────────────────────────── поле на всю ширину ───────────────────────────
// недельный период обязан занимать всю ширину: раньше слот был зашит 33 px,
// и на широком экране справа оставалась пустая полоса ~360 px
await page.click("[data-preset='7']");
await page.waitForTimeout(900);
const fit = await page.evaluate(() => {
  const g = document.querySelector("[data-vp-grid]");
  const inner = document.querySelector("[data-vp-print]");
  return {
    avail: g.clientWidth,
    inner: Math.round(inner.getBoundingClientRect().width),
    slot: Math.round(document.querySelector("[data-day]").getBoundingClientRect().width / 4),
  };
});
ok(Math.abs(fit.inner - fit.avail) <= 2,
   `поле дотянуто до правого края: ${fit.inner}px при ${fit.avail}px доступных`);
ok(fit.slot > 33, `слот на широком экране стал шире: ${fit.slot}px вместо зашитых 33px`);
await page.click("[data-preset='31']");
await page.waitForTimeout(700);

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
await page.fill('[data-field="start-hour"]', "06:00");
await page.fill('[data-field="end-date"]', D2);
await page.fill('[data-field="end-hour"]', "12:00");
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

// то же время, но свободные метры — ставить можно. 40 м на 80-м метру: до
// борта первого судна ровно 10 м — меньше уже нельзя, а больше оставлено
// нарочно: дальше по сценарию первое судно ездит по причалу и должно иметь куда
// встать, не наехав на соседа
await page.fill('[data-field="vessel"]', "SECOND SHIP");
await page.fill('[data-field="length-m"]', "40");
await page.fill('[data-field="offset-m"]', "80");
await page.click("[data-admin-action='save-placement']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached", timeout: 8000 });
// Полоса ждётся отдельно: модалка закрывается в тот же момент, когда `load()`
// ещё не доехал, и подсчёт полос сразу после неё ловил «0 штук» на ровном месте
// (прогон падал ровно на этой проверке, хотя постановка стояла в базе).
await page.waitForSelector("[data-vp-bar] >> text=SECOND SHIP", { timeout: 8000 });
ok((await page.locator("[data-vp-bar]").filter({ hasText: "SECOND SHIP" }).count()) === 1,
   "второе судно в то же время встало, если метры не пересекаются");

// Убираем второе судно: с ним на стенке не остаётся места даже без зазора, а
// подсказку мы проверяем на «вот где встанешь», а не на «места нет».
const secondShip = (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json()))
  .placements.find((p) => p.vessel === "SECOND SHIP");
if (secondShip) await api(`/api/vessels/placements/${secondShip.id}`, "DELETE");
await page.click("button:has-text('Обновить')");
await page.waitForTimeout(600);

// подсказка свободного места: не заставляем гадать и ловить 409. не 60-й метр
// (борт в борт), а 70-й: между корпусами обязано остаться 10 м
await page.click("[data-admin-action='new-placement']");
await page.waitForSelector("[data-modal='placement-form']");
await page.selectOption('[data-field="berth"]', String(testBerth.id));
await page.fill('[data-field="start-date"]', plus(4));
await page.fill('[data-field="end-date"]', plus(4));
await page.fill('[data-field="length-m"]', "10");
await page.click("[data-admin-action='find-free']");
await page.waitForSelector("[data-free-hint]", { timeout: 8000 });
const freeOffset = await page.inputValue('[data-field="offset-m"]');
ok(freeOffset === "70", `подсказка свободного места держит зазор: ${freeOffset} м, `
   + `${await page.textContent("[data-free-hint]")}`);
await page.click("[data-modal='placement-form'] [data-admin-action='close-modal']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached" });
// Возвращаем второе судно: дальше по сценарию полосу водят именно на него, и
// без него отказ ловить было бы не на чем.
if (secondShip) {
  await api("/api/vessels/placements", "POST", {
    berth_id: secondShip.berth_id, lane: secondShip.lane, vessel: secondShip.vessel,
    length_m: secondShip.length_m, offset_m: secondShip.offset_m,
    starts_at: secondShip.starts_at, ends_at: secondShip.ends_at,
  });
  await page.click("button:has-text('Обновить')");
  await page.waitForTimeout(700);
}



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

// ──────────────────────────────────────── PDF ──────────────────────────────────
// Лист рисует бэкенд, браузер его только качает. Значит, проверяем два: какой
// кусок плана уехал на сервер (он обязан равняться видимому кадру) и что назад
// прилетел настоящий файл, а не страница кабинета.
const pdfBtn = page.locator("[data-admin-action='export-pdf']");
ok((await pdfBtn.count()) === 1, "кнопка «⤓ PDF» на месте");

// сдвигаем кадр: в PDF должно уйти ровно то, что видно, а не весь период
await page.evaluate(() => {
  const g = document.querySelector("[data-vp-grid]");
  g.scrollLeft = 700;
  g.scrollTop = 120;
});
await page.waitForTimeout(500);
const frame = await page.evaluate(() => {
  const g = document.querySelector("[data-vp-grid]").getBoundingClientRect();
  const days = [...document.querySelectorAll("[data-vp-grid] [data-day]")]
    .filter((d) => { const r = d.getBoundingClientRect(); return r.right > g.left + 1 && r.left < g.right - 1; })
    .map((d) => d.dataset.day);
  const berthIds = [...document.querySelectorAll("[data-vp-grid] [data-berth-block]")]
    .filter((n) => { const r = n.getBoundingClientRect(); return r.bottom > g.top + 1 && r.top < g.bottom - 1; })
    .map((n) => Number(n.dataset.berthId));
  return { days, berthIds, totalDays: document.querySelectorAll("[data-vp-grid] [data-day]").length };
});
ok(frame.days.length < frame.totalDays, `кадр меньше периода: видно ${frame.days.length} сут из ${frame.totalDays}`);

// ответ задерживаем намеренно: кнопка обязана стать неактивной, пока файл летит,
// иначе диспетчер нажмёт «⤓ PDF» десять раз и десять раз пойдёт в бэкенд
await page.route((url) => url.pathname.endsWith("/api/vessels/plan.pdf"), async (route) => {
  await new Promise((r) => setTimeout(r, 500));
  await route.continue();
});

const resPromise = page.waitForResponse((r) => r.url().includes("/api/vessels/plan.pdf"));
const dlPromise = page.waitForEvent("download");
await pdfBtn.click();
ok(await pdfBtn.isDisabled(), "пока PDF летит, кнопка неактивна и повторный клик не пройдёт");
const pdfRes = await resPromise;
const download = await dlPromise;

const q = new URL(pdfRes.url()).searchParams;
const askedDays = Number(q.get("day_to")) - Number(q.get("day_from")) + 1;
ok(askedDays === frame.days.length,
   `на сервер ушли только видимые сутки: ${askedDays} из ${frame.totalDays} (${frame.days[0]} … ${frame.days[frame.days.length - 1]})`);
const askedBerths = (q.get("berth_ids") || "").split(",").filter(Boolean).map(Number);
ok(askedBerths.length > 0 && askedBerths.length === frame.berthIds.length
   && askedBerths.every((id) => frame.berthIds.includes(id)),
   `на сервер ушли причалы из кадра: ${askedBerths.join(", ")} (в кадре ${frame.berthIds.join(", ")})`);

// тело ответа браузер уже отдал загрузке, поэтому смотрим на сам сохранённый
// файл: это ровно то, что диспетчер увидит на диске
const savedPath = await download.path();
const file = savedPath ? readFileSync(savedPath) : Buffer.alloc(0);
ok(pdfRes.status() === 200 && file.subarray(0, 5).toString("latin1") === "%PDF-" && file.length > 2000,
   `браузер сохранил настоящий PDF, а не страницу кабинета: ${file.length} байт`);
ok((pdfRes.headers()["content-type"] || "").startsWith("application/pdf"), "ответ помечен как application/pdf");
ok(/attachment/.test(pdfRes.headers()["content-disposition"] || ""), "файл уходит как вложение, а не открывается во вкладке");

const name = await download.suggestedFilename();
ok(/^план_постановки_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.pdf$/.test(name),
   `файл назван по видимому окну, а не по периоду: ${name}`);

await page.waitForSelector("[data-saved-note]");
ok(true, "после выгрузки показана плашка «PDF готов»");
await page.waitForTimeout(600);
ok(!(await pdfBtn.isDisabled()), "PDF дошёл — кнопка снова работает: следующий кусок берут этим же кадром");
await page.unroute((url) => url.pathname.endsWith("/api/vessels/plan.pdf"));

await page.evaluate(() => { const g = document.querySelector("[data-vp-grid]"); g.scrollLeft = 0; g.scrollTop = 0; });
await page.waitForTimeout(400);

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
const slotPx = await page.evaluate(() =>
  Math.round(document.querySelector("[data-day]").getBoundingClientRect().width / 4));
const box = await page.locator(`[data-vp-bar][data-offset-m="${before.offset}"]`)
  .filter({ hasText: VESSEL }).first().boundingBox();
await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
await page.mouse.down();
await page.mouse.move(box.x + box.width / 2 + slotPx, box.y + box.height / 2 + 12, { steps: 6 });
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
ok(Math.abs(after.left - before.left - slotPx) <= 2,
   `мышью передвинули по времени: +1 слот (${after.left - before.left}px при слоте ${slotPx}px)`);
ok((await page.locator("[data-modal='placement-detail']").count()) === 0,
   "после перетаскивания карточка не открылась (клик не подменился)");

// наезжаем на другое судно — бэкенд не даёт, и полоса возвращается на место
const box2 = await page.locator(`[data-vp-bar][data-offset-m="10"]`).filter({ hasText: VESSEL })
  .first().boundingBox();
await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2);
await page.mouse.down();
await page.mouse.move(box2.x + box2.width / 2, box2.y + box2.height / 2 + 24, { steps: 6 });
await page.mouse.up();
// Бэкенд обязан ответить отказом и вернуть полосу на место. Каким именно
// словом — зависит от того, наехал ли корпус на чужой или встал к нему впритык:
// «пересечение по длине» либо «между бортами N м».
await page.waitForFunction(() => /пересечение по длине|между бортами/.test(document.querySelector("[role=alert]")?.textContent ?? ""), { timeout: 8000 });
const dragErr = (await page.textContent("[role=alert]")) || "";
ok(/пересечение по длине|между бортами/.test(dragErr), `съехало на другое судно — не пустило: «${dragErr}»`);
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
ok((await page.locator("[data-modal='placement-detail']").count()) === 1,
   "без карандаша полоса осталась кнопкой: клик открыл карточку");
await page.keyboard.press("Escape");
await page.waitForSelector("[data-modal='placement-detail']", { state: "detached", timeout: 8000 });

// ───────────────────── перетаскивание между причалами ─────────────────────
await pencil.click();
await page.waitForSelector("[data-wysiwyg-hint]");
const second = await api("/api/vessels/berths", "POST", { code: BERTH2_CODE, length_m: 200, lanes: 2 })
  .then((r) => r.json());
ok(second.code === BERTH2_CODE, `второй тестовый причал ${BERTH2_CODE} создан`);
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector("[data-berth-block]");
await page.waitForTimeout(600);
await page.click("[data-admin-action='wysiwyg']");
await page.waitForSelector("[data-wysiwyg-hint]");

const src = await page.locator(`[data-vp-bar]`).filter({ hasText: VESSEL }).first().boundingBox();
const dst = await page.locator(`[data-berth-block="${BERTH2_CODE}"] [data-berth-scale]`).boundingBox();
ok(!!src && !!dst && dst.y > src.y, `цель ниже по сетке: ${dst?.y} > ${src?.y}`);
await page.mouse.move(src.x + 30, src.y + src.height / 2);
await page.mouse.down();
await page.mouse.move(src.x + 30, dst.y + 60, { steps: 14 });
await page.waitForTimeout(200);
const overTarget = await page.evaluate((code) => {
  const el = [...document.querySelectorAll("[data-berth-block]")].find((b) => b.dataset.berthBlock === code);
  return el?.dataset.dropTarget === "1";
}, BERTH2_CODE);
ok(overTarget, "причал-цель подсвечивается, пока над ним курсор");
await page.mouse.move(src.x + 30, dst.y + 60, { steps: 2 });
await page.mouse.up();
await page.waitForFunction(
  ({ name, code }) => [...document.querySelectorAll("[data-vp-bar]")].some(
    (b) => b.textContent.includes(name) && b.dataset.berth === code,
  ),
  { name: VESSEL, code: BERTH2_CODE },
  { timeout: 8000 },
);
const moved = await page.evaluate((name) => {
  const el = [...document.querySelectorAll("[data-vp-bar]")].find((b) => b.textContent.includes(name));
  return el ? { berth: el.dataset.berth, offset: el.dataset.offsetM } : null;
}, VESSEL);
ok(moved?.berth === BERTH2_CODE,
   `судно переехало между причалами: ${moved?.berth} · ${moved?.offset} м`);
const saved2 = (await page.textContent("[data-saved-note]")) || "";
ok(saved2.includes(BERTH2_CODE), `и это подписано в ответе: «${saved2}»`);
await pencil.click();
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
// план грузится асинхронно: без ожидания полос проверка «менеджер план видит»
// выигрывает гонку у первого ответа API и падает на ровном месте
await page.waitForSelector("[data-vp-bar]", { timeout: 8000 }).catch(() => undefined);
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
  await page.waitForFunction(
    (name) => [...document.querySelectorAll("[data-vp-bar]")].some((b) => b.textContent.includes(name)),
    MV, { timeout: 8000 },
  );
  ok(true, "менеджер по настройке реально поставил судно");

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
const delBar = page.locator(`[data-vp-bar]`).filter({ hasText: VESSEL }).first();
const delBox = await delBar.boundingBox();
// перетаскивание выключено, но клик по полосе обязан открывать карточку
await page.mouse.click(delBox.x + delBox.width / 2, delBox.y + delBox.height / 2);
await page.waitForSelector("[data-modal='placement-detail']", { timeout: 8000 });
await page.click("[data-admin-action='delete-placement']");
await page.waitForSelector("[data-modal='placement-detail']", { state: "detached", timeout: 8000 });
await page.waitForFunction(
  (name) => ![...document.querySelectorAll("[data-vp-bar]")].some((b) => b.textContent.includes(name)),
  VESSEL, { timeout: 8000 },
);
ok(true, "постановка удалена");


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

// ───────────────────────── стрелки перешвартовок ─────────────────────
// Цепочка из нескольких перестановок одного судна: раньше все стрелки были
// одного цвета, без подписей и различий — «паутина», где непонятно, какая линия
// куда ведёт. Теперь у каждой есть подпись шага (теми же словами, что печатает
// PDF), кружок «откуда», а чужие цепочки уходят на фон под курсором.
// Проверка стоит в самом конце прогона не случайно: наведение и клик по полосе
// могут прокрутить сетку, а мышиные проверки выше считают координаты полос по
// boundingBox() — сдвинутый скролл меняет их цели. В конце скролл возвращается.
await page.click("[data-nav='vessels']");
await page.waitForSelector("[data-vp-bar]", { timeout: 8000 });
await page.waitForTimeout(700);
const scroll0 = await page.evaluate(() => {
  const g = document.querySelector("[data-vp-grid]");
  return { x: g.scrollLeft, y: g.scrollTop };
});
const restoreScroll = () =>
  page.evaluate((s) => {
    const g = document.querySelector("[data-vp-grid]");
    g.scrollLeft = s.x;
    g.scrollTop = s.y;
  }, scroll0);
const arrowRows = () =>
  page.evaluate(() =>
    [...document.querySelectorAll("[data-vp-arrows] [data-arrow]")].map((g) => ({
      id: Number(g.dataset.arrow),
      tone: g.dataset.arrowTone,
      opacity: Number(getComputedStyle(g).opacity),
      text: g.querySelector("text")?.textContent ?? "",
      circle: !!g.querySelector("circle"),
      d: (g.querySelector("path")?.getAttribute("d") ?? "").trim(),
    })),
  );

{
  const rows = await arrowRows();
  const plan = await api(`/api/vessels/placements?from=${plus(0)}&to=${plus(30)}`).then((r) => r.json());
  const ids = new Set(plan.placements.map((p) => p.id));
  // непустая связь цепочки = копия со стрелкой (состоявшаяся — уже без неё)
  const links = plan.placements.filter(
    (p) => !p.committed && p.moved_from_id && ids.has(p.moved_from_id),
  );
  ok(rows.length === links.length,
     `стрелок ровно по числу непустых связей цепочки: ${rows.length} (в данных ${links.length})`);
  // «3» было бы красиво, но это не проверка, а охрана музейного фонда: связи
  // считаются по живой базе, а диспетчер гоняет МАРИНУ по фронту и за один вечер
  // делает цепочку длиннее. Настоящий инвариант — предыдущая проверка (стрелок
  // ровно по числу связей); здесь лишь бы демо-цепочка не засохла до одной.
  ok(links.length >= 3, `в демо-цепочке «112» перестановок и стрелок не меньше трёх: ${links.length}`);
  ok(rows.length > 0 && rows.every((r) => r.text.includes("шаг ")),
     `у каждой стрелки есть подпись шага: ${rows.map((r) => `«${r.text}»`).join(" / ")}`);
  ok(
    rows.length > 0 &&
      rows.every((r) => /^шаг \d+→\d+ · .+ · \d{2}\.\d{2} \d{2}:\d{2}$/.test(r.text)),
    "подпись собрана как в PDF: «шаг N→M · причал · дд.мм чч:мм»",
  );
  ok(rows.length > 0 && rows.every((r) => r.circle), "у каждой стрелки есть кружок «откуда»");
  // Краска стрелки обязана быть темнее фона: светлая краска палитры на светлой
  // сетке и на чужой залитой полосе исчезала — «стрелочки не видно». Порог
  // 4.5:1 к белому (WCAG AA) и на экране, и на бумаге считается одной и той же
  // формулой (`arrowInk` / `_ink`), поэтому проверка здесь — за оба носителя.
  const paint = await page.evaluate(() => {
    const rel = (hex) => hex.replace("#", "").match(/../g).map((x) => parseInt(x, 16) / 255)
      .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    const rows = [...document.querySelectorAll("[data-vp-arrows] [data-arrow]")].map((g) => {
      const p = g.querySelector("path");
      const v = rel(p.getAttribute("stroke"));
      return {
        contrast: 1.05 / (0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2] + 0.05),
        tone: g.dataset.arrowTone,
        width: Number(p.getAttribute("stroke-width")),
        ring: g.querySelector("circle")?.getAttribute("stroke"),
      };
    });
    return {
      worst: Math.min(...rows.map((r) => r.contrast)),
      base: Math.min(...rows.filter((r) => r.tone === "base").map((r) => r.width), 99),
      casings: document.querySelectorAll("[data-arrow-casings] path").length,
      noCasing: rows.filter((r) => r.tone === "dim").length,
      total: rows.length,
      rings: rows.every((r) => r.ring === "#ffffff" || r.ring === "white"),
    };
  });
  ok(paint.worst >= 4.5,
     `краска стрелок контрастнее 4.5:1 к белому: худшая ${paint.worst.toFixed(2)}:1`);
  ok(paint.base >= 3, `обычная стрелка не тоньше 3 px: ${paint.base} px`);
  ok(paint.casings === paint.total - paint.noCasing,
     `под видимыми стрелками белая подложка (${paint.casings} из ${paint.total}; у «чужих» её нет)`);
  ok(paint.rings, `кружок «откуда» обведён белым кольцом, чтобы не сливаться с полосой`);
  // стрелка — прямая «из А в Б»: ровно две точки, без углов и промежуточных звеньев
  const crooked = rows.filter((r) => !/^M [-\d.]+ [-\d.]+ L [-\d.]+ [-\d.]+$/.test(r.d));
  ok(rows.length > 0 && crooked.length === 0,
     `стрелки — прямые отрезки «из А в Б»: ${rows.length - crooked.length} из ${rows.length}` +
       (crooked.length ? `, с углами остались id ${crooked.map((r) => r.id).join(", ")}` : ""));
  const legend = (await page.textContent("[data-arrows-legend]")) || "";
  ok(legend.includes("шаг N→M") && legend.includes("кружок"),
     `подсказка внизу объясняет стрелки: «${(legend.match(/Стрелка перешвартовки[^]*?фон/) || ["—"])[0]}…»`);
  const over = await page.evaluate(() => {
    const print = document.querySelector("[data-vp-print]");
    return Math.round(print.scrollWidth - print.clientWidth);
  });
  ok(over <= 1, `подписи не раздули холст и не добавили горизонтального скролла: ${over}px`);
}

// ────── своя цепочка яркая, чужие — на фоне (hover и «выбор» без hover) ────── //
// Без второй цепочки проверка была бы честным словом «все свои»: тускнуть нечему.
const CHAIN = `QA SHIP CHAIN ${Date.now().toString(36)}`;
const chainFrom = await api("/api/vessels/placements", "POST", {
  berth_id: testBerth.id, lane: 1, vessel: CHAIN, cargo: "СОЛЬ", length_m: 40, offset_m: 0,
  starts_at: `${plus(6)}T00:00:00`, ends_at: `${plus(6)}T12:00:00`,
}).then((r) => r.json());
const chainTo = await api("/api/vessels/placements", "POST", {
  berth_id: testBerth.id, lane: 1, vessel: CHAIN, cargo: "СОЛЬ", length_m: 40, offset_m: 0,
  starts_at: `${plus(8)}T00:00:00`, ends_at: `${plus(8)}T12:00:00`, moved_from_id: chainFrom.id,
}).then((r) => r.json());
await page.click("button:has-text('Обновить')");
await page.waitForSelector(`[data-vp-bar="${chainTo.id}"]`, { timeout: 8000 });
{
  const all = await arrowRows();
  ok(all.length >= 2, `вторая цепочка дала свою стрелку — всего ${all.length}`);
  // наводимся на ВТОРУЮ полосу своей цепочки — это копия, к ней ведёт стрелка
  await page.hover(`[data-vp-bar="${chainTo.id}"]`);
  await page.waitForTimeout(300);
  const hov = await arrowRows();
  const bright = hov.filter((r) => r.opacity > 0.9);
  const dim = hov.filter((r) => r.opacity < 0.2);
  ok(bright.some((r) => r.id === chainTo.id) && bright.every((r) => r.tone === "focus"),
     `наведение оставило яркими стрелки своей цепочки: ${bright.map((r) => r.id).join(", ")}`);
  ok(dim.length > bright.length,
     `чужие стрелки ушли в фон: тусклых ${dim.length}, ярких ${bright.length}`);
  const rings = await page.evaluate(() =>
    [...document.querySelectorAll('[data-chain-focus="1"]')].map((el) => Number(el.dataset.vpBar)),
  );
  ok(rings.includes(chainFrom.id) && rings.includes(chainTo.id),
     `оригинал и копия этой стрелки получили обводку: ${rings.join(", ")}`);

  // мобильного hover нет: тот же акцент обязана давать открытая панель деталей
  await page.click(`[data-vp-bar="${chainTo.id}"]`);
  await page.waitForSelector("[data-modal='placement-detail']");
  await page.mouse.move(4, 4); // курсор убран с полосы — подсветка должна остаться на «выборе»
  await page.waitForTimeout(300);
  const sel = await arrowRows();
  ok(
    sel.some((r) => r.id === chainTo.id && r.opacity > 0.9) &&
      sel.filter((r) => r.opacity < 0.2).length > 0,
    "без hover, по открытой карточке: своя стрелка яркая, чужие на фоне",
  );
  await page.keyboard.press("Escape");
  await page.waitForSelector("[data-modal='placement-detail']", { state: "detached", timeout: 8000 });
}
await api(`/api/vessels/placements/${chainTo.id}`, "DELETE");
await api(`/api/vessels/placements/${chainFrom.id}`, "DELETE");
await page.click("button:has-text('Обновить')");
await page.waitForTimeout(600);
await restoreScroll();

// -------------------------------------------- нахлёст на соседний причал ----
{
  // Два своих причала рядом по фронту (100 м и 110 м). Соседство обязано быть
  // настоящим: нахлёст разрешён только туда, куда корпус лезет физически, —
  // поэтому сортировку ставим вручную и проверяем отказ «не на тот причал».
  const tag = Date.now().toString(36);
  const sA = await api("/api/vessels/berths", "POST", { code: `QA-S${tag}`, length_m: 100, sort_order: 800 }).then((r) => r.json());
  const sB = await api("/api/vessels/berths", "POST", { code: `QB-T${tag}`, length_m: 110, sort_order: 801 }).then((r) => r.json());
  const dates = { starts_at: `${plus(120)}T00:00:00`, ends_at: `${plus(122)}T00:00:00` };
  const SPILL_SHIP = "QA SHIP SPILL";

  const long = await api("/api/vessels/placements", "POST", {
    berth_id: sA.id, lane: 1, vessel: SPILL_SHIP, length_m: 130, offset_m: 0,
    spill_berth_id: sB.id, ...dates,
  }).then((r) => r.json());
  ok(long.spill_berth_id === sB.id, `судно длиннее причала встало и назвало соседа: ${long.detail || sB.code}`);

  const busy = await api("/api/vessels/placements", "POST", {
    berth_id: sB.id, lane: 1, vessel: "QA SHIP GUEST", length_m: 20, offset_m: 10, ...dates,
  });
  const busyText = (await busy.json().catch(() => ({})))?.detail || "";
  ok(busy.status === 409 && busyText.includes(SPILL_SHIP) && /заходит/.test(busyText),
     `метры соседа заняты по-настоящему: ${busy.status} ${String(busyText).slice(0, 70)}`);

  // Причал нахлёста — любой живой, а не «сосед по сортировке»: порядок блоков на
  // экране не равен порядку участков у воды. Проверяем, что метры бронируются именно
  // там, куда указали, и что «сосед по списку» между ними остаётся свободным.
  const sC = await api("/api/vessels/berths", "POST", { code: `QA-U${tag}`, length_m: 100, sort_order: 802 }).then((r) => r.json());
  // свои сутки: на тех же, что и первая постановка, суда честно столкнутся —
  // и проверка «можно любому причалу» превратится в проверку конфликта
  const farDates = { starts_at: `${plus(124)}T00:00:00`, ends_at: `${plus(125)}T00:00:00` };
  const far = await api("/api/vessels/placements", "POST", {
    berth_id: sA.id, lane: 1, vessel: "QA SHIP FAR", length_m: 130, offset_m: 0,
    spill_berth_id: sC.id, ...farDates,
  }).then((r) => r.json());
  ok(far.spill_berth_id === sC.id, `корпус можно отдать любому причалу, не только соседу: ${far.detail || sC.code}`);
  const farBusy = await api("/api/vessels/placements", "POST", {
    berth_id: sC.id, lane: 1, vessel: "QA SHIP FAR2", length_m: 20, offset_m: 0, ...farDates,
  });
  ok(farBusy.status === 409, `и метры заняты именно на названном причале: ${farBusy.status}`);
  await api(`/api/vessels/placements/${far.id}`, "DELETE");

  const noSpill = await api("/api/vessels/placements", "POST", {
    berth_id: sA.id, lane: 1, vessel: "QA SHIP NONE", length_m: 130, offset_m: 0, ...dates,
  });
  const noSpillText = (await noSpill.json().catch(() => ({})))?.detail || "";
  ok(noSpill.status === 400 && noSpillText.includes(sB.code),
     `без названного соседа не встанет, и подсказка говорит куда: ${String(noSpillText).slice(0, 70)}`);

  await page.fill("[data-period-from]", plus(119));
  await page.fill("[data-period-to]", plus(128));
  await page.waitForTimeout(900);
  const band = await page.evaluate((id) => {
    const el = document.querySelector(`[data-spill="${id}"]`);
    if (!el) return null;
    const cs = getComputedStyle(el);
    return {
      berth: el.closest("[data-berth-block]")?.dataset.berthBlock,
      border: cs.borderTopStyle,
      h: Math.round(el.getBoundingClientRect().height),
      title: el.getAttribute("title"),
    };
  }, String(long.id));
  ok(!!band && band.berth === sB.code && band.border === "dashed",
     `на плане чужой корпус заштрихован в блоке соседа ${sB.code}: ${JSON.stringify(band)}`);
  ok(!!band && band.h >= 24, `высота полосы = 30 м стенки соседа (${band?.h}px)`);

  await page.locator(`[data-berth-block="${sA.code}"] [data-vp-bar]`).filter({ hasText: SPILL_SHIP }).first().click();
  await page.waitForSelector("[data-modal='placement-detail']");
  const spillNote = (await page.textContent("[data-detail-spill]")) || "";
  ok(spillNote.includes(sB.code) && /30 м/.test(spillNote), `в карточке видно, кто стоит на соседе: «${spillNote.slice(0, 80)}»`);
  await page.keyboard.press("Escape");
  await page.waitForSelector("[data-modal='placement-detail']", { state: "detached", timeout: 8000 });

  // окно постановки: сосед выбирается сразу рядом с причалом, и только правильный
  await page.click("[data-admin-action='new-placement']");
  await page.waitForSelector("[data-modal='placement-form']");
  await page.selectOption("[data-field='berth']", String(sA.id));
  await page.fill("[data-field='length-m']", "130");
  await page.fill("[data-field='offset-m']", "0");
  await page.waitForTimeout(300);
  const spillUi = await page.evaluate(() => {
    const sel = document.querySelector("[data-field='spill-berth']");
    return {
      options: [...sel.options].map((o) => o.textContent.trim()),
      hint: sel.closest("label,div")?.parentElement?.textContent?.replace(/\s+/g, " ") || "",
    };
  });
  ok(
    spillUi.options.some((o) => o.includes(sB.code) && /ниже по фронту/.test(o)) &&
      spillUi.options.some((o) => /выше по фронту/.test(o)) &&
      spillUi.options.some((o) => o.includes(sC.code)),
    `в окне есть и предыдущий, и следующий причал, и другие: ${spillUi.options.length} вариантов`,
  );
  ok(/30 м/.test(spillUi.hint) && spillUi.hint.includes(sB.code),
     `подсказка говорит, куда лезет хвост: «${spillUi.hint.slice(spillUi.hint.indexOf("Выходит"), spillUi.hint.indexOf("Выходит") + 90)}»`);
  await page.keyboard.press("Escape");
  await page.waitForSelector("[data-modal='placement-form']", { state: "detached", timeout: 8000 });

  // Перетаскивание: судно водят по любой шкале и ставят так, что корпус лезет за
  // конец причала — раньше полосу клампило к «сколько влезает в блок».
  const dragShip = await api("/api/vessels/placements", "POST", {
    berth_id: sA.id, lane: 1, vessel: "QA SHIP DRAG", length_m: 60, offset_m: 0,
    starts_at: `${plus(126)}T00:00:00`, ends_at: `${plus(127)}T00:00:00`,
  }).then((r) => r.json());
  ok(!!dragShip.id, "судно для проверки перетаскивания создано");
  // строку создал API, а не форма — сетка обязана её перезапросить
  await page.click("button:has-text('Обновить')");
  await page.waitForTimeout(900);
  await page.click("[data-admin-action='wysiwyg']");
  await page.waitForSelector("[data-wysiwyg-hint]");
  await page.evaluate((code) => {
    const el = [...document.querySelectorAll("[data-berth-block]")].find((b) => b.dataset.berthBlock === code);
    el?.scrollIntoView({ block: "center" });
  }, sA.code);
  await page.waitForTimeout(400);
  const barBox = await page.locator(`[data-berth-block="${sA.code}"] [data-vp-bar]`).filter({ hasText: "QA SHIP DRAG" }).first().boundingBox();
  const scaleBox = await page.locator(`[data-berth-block="${sB.code}"] [data-berth-scale]`).boundingBox();
  ok(!!barBox && !!scaleBox, `полоса и шкала цели видны: ${!!barBox}/${!!scaleBox}`);
  // цель — 70 м причала sB (110 м): 70 + 60 = 130 м, то есть 20 м за концом.
  // Шаг шкалы — 12 px на 10 м, поэтому 70 м это 84 px, а не 840: промахивались
  // вниз и улетали в «Ожидание», где метража нет и проверка молчит
  // берём полосу за самый верх: смещение считается от захвата, и цель по курсору
  // тогда совпадает с отметкой, куда судно встанет
  await page.mouse.move(barBox.x + 30, barBox.y + 4);
  await page.mouse.down();
  await page.mouse.move(scaleBox.x + 40, scaleBox.y + (70 / 10) * 12 + 4, { steps: 14 });
  await page.waitForTimeout(150);
  await page.mouse.up();
  await page.waitForTimeout(1500);
  const plan2 = await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json());
  const afterDrag = plan2.placements.find((x) => x.id === dragShip.id);
  const whereBerth = plan2.berths.find((b) => b.id === afterDrag?.berth_id)?.code ?? "?";
  const ontoBerth = plan2.berths.find((b) => b.id === afterDrag?.spill_berth_id)?.code ?? "—";
  ok(afterDrag?.berth_id === sB.id && afterDrag?.offset_m >= 60,
     `судно перетащено на другой причал и за его габарит: ${afterDrag?.offset_m} м на ${whereBerth}`);
  ok(afterDrag?.spill_berth_id === sC.id,
     `и хвост автоматически отдан следующему причалу: ${ontoBerth}`);
  // Уводим «QA SHIP DRAG» с дороги: он отработал свою проверку и лежит на sB
  // ровно там, куда сейчас поедет хвост длинного корпуса. Оставь его — и отказ
  // был бы законен: между бортами вышло бы 0 м.
  await api(`/api/vessels/placements/${dragShip.id}`, "DELETE");
  await page.click("button:has-text('Обновить')");
  await page.waitForTimeout(700);

  // Свободное скольжение через границы своего причала: длинный корпус водят и за
  // конец шкалы, и за её начало. Полоса обязана идти за курсором, а не
  // перепрыгивать в начало соседнего причала на каждой границе.
  const longBar = await page.locator(`[data-berth-block="${sA.code}"] [data-vp-bar]`).filter({ hasText: SPILL_SHIP }).first().boundingBox();
  await page.mouse.move(longBar.x + 30, longBar.y + 4);
  await page.mouse.down();
  await page.mouse.move(longBar.x + 30, longBar.y + 4 + 48, { steps: 12 });
  await page.waitForTimeout(150);
  await page.mouse.up();
  await page.waitForTimeout(1500);
  const down = (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json()))
    .placements.find((x) => x.id === long.id);
  const downErr = (await page.textContent("[role=alert]").catch(() => "")) || "";
  ok(down?.berth_id === sA.id && down?.offset_m === 40,
     `корпус скользит за конец шкалы, причал остаётся свой: ${down?.offset_m} м на ${sA.code} ${downErr.slice(0, 130)}`);

  // Перетаскивание через верхнюю границу. Полоса обязана идти за курсором, а не
  // упираться в край своего причала: «сюда тебе нельзя, встань обратно» — это то,
  // что диспетчер называет магнитом. Причал при этом меняется на тот, что под
  // рукой, поэтому мериим не отметку (она считается от шкалы своего причала), а
  // метры фронта: корпус уехал ровно туда, куда его увели.
  const absOf = (plan, p) => {
    const bs = plan.berths.filter((b) => b.is_active);
    const parkB = bs.find((b) => b.code === "ОЖИДАНИЕ");
    const order = [...bs.filter((b) => b !== parkB).sort((x, y) => (x.sort_order - y.sort_order) || (x.id - y.id)),
      ...(parkB ? [parkB] : [])];
    let acc = 0;
    for (const b of order) { if (b.id === p?.berth_id) return acc + (p.offset_m ?? 0); acc += b.length_m ?? 0; }
    return NaN;
  };
  const planUp0 = await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json());
  const beforeAbs = absOf(planUp0, planUp0.placements.find((x) => x.id === long.id));
  const longBar2 = await page.locator(`[data-berth-block="${sA.code}"] [data-vp-bar]`).filter({ hasText: SPILL_SHIP }).first().boundingBox();
  await page.mouse.move(longBar2.x + 30, longBar2.y + 4);
  await page.mouse.down();
  await page.mouse.move(longBar2.x + 30, longBar2.y + 4 - 84, { steps: 14 });
  await page.waitForTimeout(150);
  await page.mouse.up();
  await page.waitForTimeout(1500);
  const plan3 = await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json());
  const up = plan3.placements.find((x) => x.id === long.id);
  const upAbs = absOf(plan3, up);
  const upBerth = plan3.berths.find((b) => b.id === up?.berth_id)?.code ?? "?";
  const upOnto = plan3.berths.find((b) => b.id === up?.spill_berth_id)?.code ?? "—";
  ok(Math.abs(beforeAbs - upAbs - 70) <= 10,
     `корпус идёт за курсором через верхнюю границу: ${beforeAbs} м → ${upAbs} м фронта (${upBerth} / ${up?.offset_m} м)`);
  ok(up?.spill_berth_id != null,
     `причал, который корпус пересёк, отдан автоматически: ${upBerth} → ${upOnto}`);

  // Хвост длиннее одного соседа. Корпус — непрерывный отрезок стенки: 300 м на
  // sA (100 м) отдаёт вперёд 200 м, это весь sB (110 м) и первые 90 м sC. Пока
  // нахлёст обрезался по длине названного причала, диспетчер видел, как большое
  // судно «уменьшилось», а третье поле осталось пустым — и на эти метры свободно
  // вставало другое судно.
  const cascadeDates = { starts_at: `${plus(126)}T00:00:00`, ends_at: `${plus(127)}T00:00:00` };
  const cascade = await api("/api/vessels/placements", "POST", {
    berth_id: sA.id, lane: 1, vessel: "QA SHIP CASCADE", length_m: 300, offset_m: 0,
    spill_berth_id: sB.id, ...cascadeDates,
  }).then((r) => r.json());
  ok(cascade?.id != null, `судно с хвостом через два причала встало: ${cascade?.detail || cascade?.id}`);
  const onThird = await api("/api/vessels/placements", "POST", {
    berth_id: sC.id, lane: 1, vessel: "QA SHIP CASCADE-2", length_m: 5, offset_m: 85, ...cascadeDates,
  });
  const thirdText = (await onThird.json().catch(() => ({})))?.detail || "";
  ok(onThird.status === 409 && String(thirdText).includes("QA SHIP CASCADE"),
     `хвост, не влезший в соседа, занят и на третьем причале: ${onThird.status} ${String(thirdText).slice(0, 60)}`);
  // и сразу за концом чужого корпуса не пустят: между бортами обязано остаться
  // 10 м, а на sC после 90 м шпата остаётся ровно 10 м стенки — пятиметровому
  // корпусу туда встаться некуда, и это правильно, а не «мелочёвка»
  const pastHull = await api("/api/vessels/placements", "POST", {
    berth_id: sC.id, lane: 1, vessel: "QA SHIP CASCADE-3", length_m: 5, offset_m: 95, ...cascadeDates,
  });
  const pastText = (await pastHull.json().catch(() => ({})))?.detail || "";
  ok(pastHull.status === 409 && /между бортами 5 м/.test(pastText),
     `и впритык за чужим хвостом не встанет: ${pastHull.status} ${String(pastText).slice(0, 60)}`);
  await page.click("button:has-text('Обновить')");
  await page.waitForTimeout(900);
  const thirdBand = await page.evaluate(([id, pid, len]) => {
    const col = document.querySelector(`[data-berth-scale="${id}"]`);
    const el = col?.querySelector(`[data-spill="${pid}"]`);
    if (!col || !el) return null;
    const r = el.getBoundingClientRect(), c = col.getBoundingClientRect();
    return `${(((r.top - c.top) / c.height) * len).toFixed(0)}–${(((r.bottom - c.top) / c.height) * len).toFixed(0)} м`;
  }, [String(sC.id), String(cascade.id), 100]);
  ok(thirdBand === "0–90 м", `на плане 90 м корпуса доехали до третьего причала: ${thirdBand}`);
  // Полоса живёт в своём блоке. Метры за его краем принадлежат другим причалам и
  // показаны там штриховкой — рисовать их ещё и полосой поверх чужого блока значит
  // рисовать один борт дважды (и воевать с полосой того, кто стоит по-настоящему).
  const inside = await page.evaluate(([code, pid]) => {
    const blk = document.querySelector(`[data-berth-block="${code}"]`);
    const bar = blk?.querySelector(`[data-vp-bar="${pid}"]`);
    if (!blk || !bar) return null;
    const b = blk.getBoundingClientRect(), r = bar.getBoundingClientRect();
    return { above: Math.round(b.top - r.top), below: Math.round(r.bottom - b.bottom) };
  }, [sA.code, String(cascade.id)]);
  ok(inside && inside.above <= 1 && inside.below <= 1,
     `полоса 300 м не лезет в чужие блоки: выше на ${inside?.above} px, ниже на ${inside?.below} px`);
  const spillLabel = await page
    .textContent(`[data-berth-block="${sC.code}"] [data-spill-label="${cascade.id}"]`)
    .catch(() => null);
  ok(!!spillLabel && /хвост/.test(spillLabel) && /90 м/.test(spillLabel),
     `штриховка подписана теми же словами, что печатает лист: «${spillLabel}»`);
  await api(`/api/vessels/placements/${cascade.id}`, "DELETE");

  await api(`/api/vessels/placements/${dragShip.id}`, "DELETE");
  await api(`/api/vessels/placements/${dragShip.id}`, "DELETE");
  for (const x of (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json())).placements) {
    if (x.vessel.startsWith("QA SHIP ")) await api(`/api/vessels/placements/${x.id}`, "DELETE");
  }
  await page.click("[data-admin-action='wysiwyg']");

  // Причал, на который выходят чужие суда, удалить нельзя: метры повиснут.
  // Нахлёст создаём заново: после проверок перетаскивания корпус `long` уже
  // смотрит носом в другую сторону, и на sB ничего не опирается.
  await api("/api/vessels/placements", "POST", {
    berth_id: sA.id, lane: 1, vessel: "QA SHIP GUARD", length_m: 120, offset_m: 0,
    spill_berth_id: sB.id, starts_at: `${plus(129)}T00:00:00`, ends_at: `${plus(130)}T00:00:00`,
  });
  const delHost = await api(`/api/vessels/berths/${sB.id}`, "DELETE");
  ok(delHost.status === 409, `причал, занятый чужим корпусом, не удаляется: ${delHost.status}`);
  await api(`/api/vessels/placements/${long.id}`, "DELETE");
  await api(`/api/vessels/berths/${sA.id}`, "DELETE");
  await api(`/api/vessels/berths/${sC.id}`, "DELETE");
  await api(`/api/vessels/berths/${sB.id}`, "DELETE");

  await page.fill("[data-period-from]", plus(0));
  await page.fill("[data-period-to]", plus(30));
  await page.waitForTimeout(600);
}

// ── зазор между бортами: 10 метров, а не «хоть впритык» ──────────────
console.log("\nЗазор между бортами");
{
  // Заказчик: «между суднами должно быть минимум расстояние 10 метров». Пока
  // запрет был только на пересечение, диспетчер ставил корпуса борт в борт: на
  // плане красиво, а у воды негде завести растропы.
  const tag = Date.now().toString(36);
  const gA = await api("/api/vessels/berths", "POST", { code: `QA-G${tag}`, length_m: 100, sort_order: 810 }).then((r) => r.json());
  const gB = await api("/api/vessels/berths", "POST", { code: `QA-H${tag}`, length_m: 100, sort_order: 811 }).then((r) => r.json());
  const gDates = { starts_at: `${plus(140)}T00:00:00`, ends_at: `${plus(141)}T00:00:00` };
  const gPost = (body) => api("/api/vessels/placements", "POST", { ...body, ...gDates });
  const host = await gPost({ berth_id: gA.id, lane: 1, vessel: "QA SHIP GAP-A", length_m: 50, offset_m: 0 });
  ok(host.status === 201, `судно 0–50 м встало: ${host.status}`);

  const tight = await gPost({ berth_id: gA.id, lane: 1, vessel: "QA SHIP GAP-B0", length_m: 10, offset_m: 50 });
  const tightText = (await tight.json().catch(() => ({})))?.detail || "";
  ok(tight.status === 409 && /между бортами 0 м/.test(tightText),
     `борт в борт больше нельзя: ${tight.status} ${String(tightText).slice(0, 70)}`);

  const almost = await gPost({ berth_id: gA.id, lane: 1, vessel: "QA SHIP GAP-B5", length_m: 10, offset_m: 55 });
  const almostText = (await almost.json().catch(() => ({})))?.detail || "";
  ok(almost.status === 409 && /между бортами 5 м/.test(almostText),
     `пяти метров между бортами мало: ${almost.status} ${String(almostText).slice(0, 60)}`);

  const fair = await gPost({ berth_id: gA.id, lane: 1, vessel: "QA SHIP GAP-B10", length_m: 10, offset_m: 60 });
  ok(fair.status === 201, `ровно 10 метров между бортами — можно: ${fair.status}`);

  // Подсказка свободного места обязана предлагать то, на чём не поймаешь отказ:
  // после корпуса 60–70 м для десятиметрового судна это 80-й метр, а не 70-й.
  const hint = await api(`/api/vessels/free-offset?berth_id=${gA.id}`
    + `&starts_at=${encodeURIComponent(`${plus(140)}T00:00:00`)}`
    + `&ends_at=${encodeURIComponent(`${plus(141)}T00:00:00`)}&length_m=10`).then((r) => r.json());
  ok(hint.offset_m === 80, `подсказка предлагает место с зазором: ${hint.offset_m} м`);

  // А вот зазор между причалами не считается: там, где кончается одна стенка,
  // начинается другая, и сколько между ними воды — в данных не записано.
  const atEnd = await gPost({ berth_id: gA.id, lane: 1, vessel: "QA SHIP GAP-EDGE", length_m: 20, offset_m: 80 });
  ok(atEnd.status === 201, `корма в 80–100 м своего причала встала: ${atEnd.status}`);
  const atStart = await gPost({ berth_id: gB.id, lane: 1, vessel: "QA SHIP GAP-EDGE2", length_m: 20, offset_m: 0 });
  ok(atStart.status === 201, `на соседнем причале у самого начала встать можно: ${atStart.status}`);

  for (const p of (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json())).placements) {
    if (p.vessel.startsWith("QA SHIP GAP")) await api(`/api/vessels/placements/${p.id}`, "DELETE");
  }
  await api(`/api/vessels/berths/${gA.id}`, "DELETE");
  await api(`/api/vessels/berths/${gB.id}`, "DELETE");
}


// ── ручное время: стоянка не обязана начинаться с начала слота ────────────
console.log("\nРучное время стоянки");
await page.fill("[data-period-from]", plus(0));
await page.fill("[data-period-to]", plus(9));
await page.waitForTimeout(700);
await page.click("[data-admin-action='new-placement']");
await page.waitForSelector("[data-modal='placement-form']");
await page.selectOption('[data-field="berth"]', String(testBerth.id));
await page.fill('[data-field="vessel"]', "QA SHIP OFFGRID");
await page.fill('[data-field="length-m"]', "60");
await page.fill('[data-field="start-date"]', plus(1));
await page.fill('[data-field="start-hour"]', "22:00");
await page.fill('[data-field="end-date"]', plus(2));
await page.fill('[data-field="end-hour"]', "06:30");
await page.click("[data-admin-action='save-placement']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached", timeout: 8000 });

const offPlan = await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json());
const off = offPlan.placements.find((x) => x.vessel === "QA SHIP OFFGRID");
ok(off?.starts_at.endsWith("T22:00:00") && off?.ends_at.endsWith("T06:30:00"),
   `окно постановки приняло 22:00 — 06:30, а не ближайший слот: ${off?.starts_at} — ${off?.ends_at}`);
if (!off) throw new Error("постановка с ручным временем не создалась — дальше идти не за чем");

{
  // Полоса режется по моменту, а не по сетке слотов. Раньше «22:00 — 06:30»
  // закрашивало две шестичасовые ячейки целиком: человек ставит «до 21:00»,
  // получает закрашенные 18–24 и идёт искать ошибку там, где её нет — проверка
  // конфликта всегда считала моменты времени, врел именно рисунок.
  const hoursDrawn = await page.evaluate((id) => {
    const scale = document.querySelector("[data-berth-scale]");
    const bar = document.querySelector(`[data-vp-bar="${id}"]`);
    if (!scale || !bar) return null;
    const pxH = scale.getBoundingClientRect().width / (10 * 24);  // период прогона — 10 суток
    return (bar.getBoundingClientRect().width + 3) / pxH;         // +3 px — отступ края полосы
  }, off.id);
  ok(hoursDrawn != null && Math.abs(hoursDrawn - 8.5) <= 0.3,
     `полоса занимает свои 8,5 ч, а не два слота по 6 ч: ${hoursDrawn?.toFixed(1)} ч`);
}

{
  // Перетаскивание по времени обязано беречь ручное время: шаг — час, но
  // длительность стоянки и её минуты уезжают вместе с судом, а не съедаются
  // шкалой. Проверка по инвариантам: сдвиг кратен часу, стоянка той же
  // длительности, минуты на месте.
  await page.click("[data-admin-action='wysiwyg']");
  await page.waitForSelector("[data-wysiwyg-hint]");
  const offBarEl = page.locator("[data-vp-bar]").filter({ hasText: "QA SHIP OFFGRID" }).first();
  await offBarEl.scrollIntoViewIfNeeded();
  const offBar = await offBarEl.boundingBox();
  await page.mouse.move(offBar.x + offBar.width / 2, offBar.y + offBar.height / 2);
  await page.mouse.down();
  await page.mouse.move(offBar.x + offBar.width / 2 + 260, offBar.y + offBar.height / 2, { steps: 12 });
  await page.mouse.up();
  await page.waitForTimeout(1400);
  const after = (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json()))
    .placements.find((x) => x.vessel === "QA SHIP OFFGRID");
  const ms = (v) => new Date(v).getTime();
  const dur0 = ms(off.ends_at) - ms(off.starts_at);
  const dur1 = ms(after.ends_at) - ms(after.starts_at);
  const shift = ms(after.starts_at) - ms(off.starts_at);
  ok(dur0 === dur1 && shift > 0 && shift % 3600e3 === 0 && ms(after.starts_at) % 60000 === 0,
     `перетаскивание сохранило ручное время: сдвиг ${shift / 3600e3} ч, стоянка ${dur1 / 3600e3} ч, ${after.starts_at} — ${after.ends_at}`);
  await page.click("[data-admin-action='wysiwyg']");
  await page.waitForTimeout(300);
}
await api(`/api/vessels/placements/${off.id}`, "DELETE");

{
  // Впритык к соседу, у которого край НЕ на сетке слотов. Перетаскивание ходит
  // часом, а не слотом: к судну, уходящему в 22:00, следующее обязано придвинуться
  // в 22:00, а шагом в 6 часов в такую точку не попасть никогда — на это и
  // жаловался диспетчер: «передвигаю к тому судну, а оно не перемещается туда».
  // Плюс магнит: час — это ~6 px, попадать в них на глаз нереально.
  const fa = await api("/api/vessels/placements", "POST", {
    berth_id: testBerth.id, lane: 1, vessel: "QA SHIP FLUSH-A", length_m: 40, offset_m: 0,
    starts_at: `${plus(1)}T00:00:00`, ends_at: `${plus(1)}T22:00:00`,
  }).then((r) => r.json());
  const fb = await api("/api/vessels/placements", "POST", {
    berth_id: testBerth.id, lane: 1, vessel: "QA SHIP FLUSH-B", length_m: 40, offset_m: 0,
    starts_at: `${plus(3)}T06:00:00`, ends_at: `${plus(3)}T14:00:00`,
  }).then((r) => r.json());
  await page.click("button:has-text('Обновить')");
  await page.waitForTimeout(900);
  await page.click("[data-admin-action='wysiwyg']");
  await page.waitForSelector("[data-wysiwyg-hint]");
  const barEl = page.locator(`[data-vp-bar="${fb.id}"]`);
  await barEl.scrollIntoViewIfNeeded();
  await page.waitForTimeout(250);
  const g = await page.evaluate(([id]) => {
    const scale = document.querySelector("[data-berth-scale]");
    const bar = document.querySelector(`[data-vp-bar="${id}"]`);
    const c = scale.getBoundingClientRect(), r = bar.getBoundingClientRect();
    return { pxH: c.width / (10 * 24), left: c.left, x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width };
  }, [String(fb.id)]);
  // целимся левым краем в 21:20 — магнит обязан довести ровно до 22:00 соседа
  const wantX = g.left + (24 + 21 + 20 / 60) * g.pxH;
  await page.mouse.move(g.x, g.y);
  await page.mouse.down();
  await page.mouse.move(wantX + g.w / 2, g.y, { steps: 16 });
  await page.waitForTimeout(200);
  const dragHint = (await page.locator("[data-vp-drag-hint]").innerText().catch(() => "НЕТ ПОДСКАЗКИ")).replace(/\s+/g, " ");
  await page.mouse.up();
  await page.waitForTimeout(1400);
  const flush = (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json()))
    .placements.find((x) => x.id === fb.id);
  ok(flush?.starts_at === `${plus(1)}T22:00:00`,
     `полосу придвинули вплотную к чужому 22:00: ${flush?.starts_at} — ${flush?.ends_at}`);
  ok(/22:00:00/.test(dragHint), `пока тащишь, видно точный срок: «${dragHint.slice(0, 46)}»`);
  await page.click("[data-admin-action='wysiwyg']");
  await page.waitForTimeout(300);
  await api(`/api/vessels/placements/${fb.id}`, "DELETE");
  await api(`/api/vessels/placements/${fa.id}`, "DELETE");
}

// ── перемещение: мышью по правой кнопке, окно — из карточки по левой ──────
console.log("\nПеремещение: копия за курсором и окно из карточки");
await page.click("[data-admin-action='new-placement']");
await page.waitForSelector("[data-modal='placement-form']");
await page.selectOption('[data-field="berth"]', String(testBerth.id));
await page.fill('[data-field="vessel"]', "QA SHIP MOVE");
await page.fill('[data-field="length-m"]', "60");
await page.fill('[data-field="start-date"]', plus(1));
await page.fill('[data-field="start-hour"]', "06:00");
await page.fill('[data-field="end-date"]', plus(2));
await page.fill('[data-field="end-hour"]', "00:00");
await page.click("[data-admin-action='save-placement']");
await page.waitForSelector("[data-modal='placement-form']", { state: "detached", timeout: 8000 });

const mvPlan0 = await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json());
const mvShip = mvPlan0.placements.find((x) => x.vessel === "QA SHIP MOVE");
if (!mvShip) throw new Error("судно для проверок перемещения не создалось");
await page.click("[data-admin-action='wysiwyg']");
await page.waitForSelector("[data-wysiwyg-hint]");
const mvBar = page.locator("[data-vp-bar]").filter({ hasText: "QA SHIP MOVE" }).first();
await mvBar.scrollIntoViewIfNeeded();
// Правая кнопка — «как было ранее»: полупрозрачная копия ездит за курсором,
// стрелка тянется из оригинала, по клику копия встаёт в точку. Точный срок и
// «хвостом на соседний причал» — окном, оно переехало в карточку судна (ниже).
await mvBar.click({ button: "right" });
await page.waitForSelector("[data-vp-menu]");
await page.click("[data-reberthing]");
await page.waitForSelector("[data-vp-ghost]");
const gs = await page.evaluate(([id]) => {
  const r = document.querySelector(`[data-berth-scale="${id}"]`).getBoundingClientRect();
  return { left: r.left, top: r.top, pxH: r.width / (10 * 24) };
}, [String(testBerth.id)]);
await page.mouse.move(gs.left + 30, gs.top + 12, { steps: 4 });
const ghostArrow = await page.locator('[data-arrow="-1"]').count();
await page.mouse.move(gs.left + 78 * gs.pxH, gs.top + 9, { steps: 14 });
const ghostChip = (await page.locator("[data-vp-ghost]").innerText()).replace(/\s+/g, " ");
ok(ghostArrow > 0 && /QA SHIP MOVE/.test(ghostChip),
   `правая кнопка — копия едет за курсором, стрелка идёт от оригинала: «${ghostChip.slice(0, 44)}»`);
await page.mouse.down();
await page.mouse.up();
await page.waitForTimeout(1500);
const gCopy = (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json()))
  .placements.find((x) => x.vessel === "QA SHIP MOVE" && x.moved_from_id === mvShip.id);
ok(gCopy?.berth_id === testBerth.id && gCopy?.starts_at === `${plus(3)}T06:00:00`,
   `копия встала в точку под курсором: ${gCopy?.starts_at} — ${gCopy?.ends_at}, ${gCopy?.offset_m} м`);
if (!gCopy) throw new Error("копия из-под курсора не появилась");

// Левая кнопка по полосе — карточка, и в ней — окно перемещения с точным сроком,
// которого мышью не добиться.
await page.locator(`[data-vp-bar="${gCopy.id}"]`).click();
await page.waitForSelector("[data-modal='placement-detail']");
await page.click("[data-admin-action='move-placement']");
await page.waitForSelector("[data-modal='move-form']");
ok(true, "карточка судна (левая кнопка) даёт окно «Временное перемещение»");
await page.fill('[data-field="move-length"]', "60");
await page.fill('[data-field="move-offset"]', "0");
await page.fill('[data-field="move-start-date"]', plus(4));
await page.fill('[data-field="move-start-hour"]', "22:00");
await page.fill('[data-field="move-end-date"]', plus(5));
await page.fill('[data-field="move-end-hour"]', "06:30");
await page.click("[data-admin-action='save-move']");
await page.waitForSelector("[data-modal='move-form']", { state: "detached", timeout: 8000 });
{
  const plan = await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json());
  const win = plan.placements.find((x) => x.vessel === "QA SHIP MOVE" && x.moved_from_id === gCopy.id);
  ok(win?.starts_at.endsWith("T22:00:00") && win?.ends_at.endsWith("T06:30:00") && win?.berth_id === testBerth.id,
     `копия из окна карточки встала в срок: ${win?.starts_at} — ${win?.ends_at}`);
  if (!win) throw new Error("копия из окна перемещения не появилась");
  await api(`/api/vessels/placements/${win.id}`, "DELETE");
  const copy = gCopy;

  // Срок правят за край полосы. Шаг — час, а не слот: диспетчер тянет «до 18:00»,
  // а не «до следующего столбца шкалы».
  const edgeBar = page.locator(`[data-vp-bar="${copy.id}"]`);
  await edgeBar.scrollIntoViewIfNeeded();
  const handle = await page.locator(`[data-vp-bar="${copy.id}"] [data-vp-edge="end"]`).boundingBox();
  ok(!!handle, "на правом крае полосы есть ручка срока");
  const before = (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json()))
    .placements.find((x) => x.id === copy.id);
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2 + 70, handle.y + handle.height / 2, { steps: 12 });
  await page.waitForTimeout(150);
  const hint = await page.locator("[data-vp-resize-hint]").innerText().catch(() => "");
  ok(/шаг — час/.test(hint), `во время правки видно точный срок, а не слот: ${hint.replace(/\s+/g, " ").slice(0, 70)}`);
  // Полоса обязана ехать за курсором, а не опережать его. Пиксели на час
  // выводим из ширины самой шкалы: деление ширины слота на 4 (слотов в сутках)
  // вместо 6 (часов в слоте) уводило превью в полтора раза вперёд — «уплывает
  // куда-то вправо, а когда заканчиваю, то норм».
  const scaleBox = await page.evaluate(() => {
    const r = document.querySelector("[data-berth-scale]").getBoundingClientRect();
    return { left: r.left, width: r.width };
  });
  const pxH = scaleBox.width / (10 * 24); // период прогона — 10 суток
  const cursorX = handle.x + handle.width / 2 + 70;
  const liveBox = await page.locator(`[data-vp-bar="${copy.id}"]`)
    .evaluate((el) => { const r = el.getBoundingClientRect(); return { left: r.left, right: r.right }; });
  const edgeHours = (liveBox.right - scaleBox.left) / pxH;
  const cursorHours = (cursorX - scaleBox.left) / pxH;
  ok(Math.abs(edgeHours - cursorHours) <= 1.2,
     `край не уезжает вперёд курсора: край на ${edgeHours.toFixed(1)} ч, курсор на ${cursorHours.toFixed(1)} ч`);
  await page.mouse.up();
  await page.waitForTimeout(1300);
  const after = (await api("/api/vessels/placements?from=2000-01-01&to=2100-01-01").then((r) => r.json()))
    .placements.find((x) => x.id === copy.id);
  const endMs = new Date(after.ends_at).getTime();
  const shiftH = (endMs - new Date(before.ends_at).getTime()) / 3600e3;
  ok(shiftH > 0 && endMs % 3600e3 === 0,
     `правый край тянет окончание часом: +${shiftH} ч → ${after.ends_at}`);
  await api(`/api/vessels/placements/${copy.id}`, "DELETE");
}
await page.click("[data-admin-action='wysiwyg']");
await api(`/api/vessels/placements/${mvShip.id}`, "DELETE");
await page.waitForTimeout(300);

await page.screenshot({ path: "shot_vessels.png", fullPage: false });
await browser.close();
// cleanup — после закрытия браузера и обязательно: упавший прогон не должен
// оставлять в dev-базе тестовые причалы, следующий прогон показывает их в сетке
try {
  await cleanup();
} catch (e) {
  console.log("чистка не удалась:", e.message);
}

console.log(problems.length ? `\nПРОБЛЕМЫ:\n${problems.join("\n")}` : "\nвсе проверки прошли");
process.exit(problems.length ? 1 : 0);
