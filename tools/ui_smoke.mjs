import { chromium } from "playwright-core";

const BASE = process.env.BASE || "http://localhost:5173";
const exe = process.env.PW_CHROMIUM;

const browser = await chromium.launch({ executablePath: exe, headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => m.type() === "error" && console.log("CONSOLE ERR:", m.text()));
page.on("pageerror", (e) => console.log("PAGE ERR:", e.message));

await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
await page.screenshot({ path: "shot_login.png" });

// негативный сценарий
await page.fill("input[autocomplete=username]", "admin");
await page.fill("input[autocomplete=current-password]", "wrong-pass");
await page.click("button.btn-primary");
await page.waitForSelector("[role=alert]", { timeout: 5000 });
console.log("alert:", await page.textContent("[role=alert]"));
await page.screenshot({ path: "shot_login_error.png" });

// позитивный сценарий
await page.fill("input[autocomplete=current-password]", process.env.PASS);
await page.click("button.btn-primary");
await page.waitForSelector("table", { timeout: 10000 });
await page.waitForTimeout(600);
console.log("url after login:", page.url());
console.log("kpi:", (await page.$$eval(".num", (els) => els.slice(0, 6).map((e) => e.textContent))).join(" | "));
await page.screenshot({ path: "shot_dashboard.png", fullPage: true });

// выход
await page.click("text=Выйти");
await page.waitForSelector("input[autocomplete=username]", { timeout: 5000 });
console.log("logout ok ->", page.url());

await browser.close();
