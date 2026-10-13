/**
 * Расчёт WCAG-контраста для матового стекла.
 *
 * Считаем ХУДШИЙ случай: текст внутри плашки стоит там, где стекло тоньше
 * всего (низ градиента 160deg, .42), а под плашкой — самое тёмное место фона
 * (голубое пятно brand.mid + линия сетки). blur(18px) на средний цвет не
 * влияет — он только размывает границы, поэтому цвет считаем как композит.
 *
 * Запуск: node tools/contrast_check.mjs
 */
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));

/** sRGB → относительная яркость по формуле WCAG 2.1 */
const lum = ([r, g, b]) => {
  const f = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};

const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
};

/** alpha-compositing: rgba поверх rgb */
const over = (fg, a, bg) => fg.map((c, i) => c * a + bg[i] * (1 - a));

const PAGE = "#eef2f8";
const INK = { "ink-100": "#0f1b2d", "ink-300": "#4a5c75", "ink-400": "#5f7189" };

// ---- фон страницы: база + пятна + сетка (порядок CSS: первый слой поверх) ----
const gridLine = over(hex("#1b3360"), 0.045, hex(PAGE)); // линия сетки 44px
const spotMid = over(hex("#337ab7"), 0.18, gridLine); // пятно 92% 2% (самое тёмное)
const spotNavy = over(hex("#1b3360"), 0.12, gridLine); // пятно 60% 104%
const spotSky = over(hex("#2fbaec"), 0.16, gridLine); // пятно 10% -6%

// ---- стекло плашки: rgba(255,255,255,.62) + градиент .78 → .42 ----
const WHITE = [255, 255, 255];
const glassThin = 1 - (1 - 0.42) * (1 - 0.62); // низ градиента = худший случай
const glassThick = 1 - (1 - 0.78) * (1 - 0.62); // верх градиента

const surfaces = {
  "стекло (худший случай: пятно brand.mid + сетка, низ градиента)": over(WHITE, glassThin, spotMid),
  "стекло (пятно navy + сетка, низ градиента)": over(WHITE, glassThin, spotNavy),
  "стекло (пятно brand.sky, верх градиента)": over(WHITE, glassThick, spotSky),
  "стекло (чистый фон, низ градиента)": over(WHITE, glassThin, gridLine),
};

console.log("=== текст поверх матового стекла (.panel) ===");
for (const [name, surf] of Object.entries(surfaces)) {
  const rgb = surf.map(Math.round);
  console.log(`\nподложка ${name}: rgb(${rgb})`);
  for (const [ink, c] of Object.entries(INK)) {
    console.log(`  ${ink.padEnd(8)} ${ratio(hex(c), rgb).toFixed(2)}:1`);
  }
}

// ---- белый текст поверх .glass-navy (шапка) ----
console.log("\n=== белый текст поверх шапки .glass-navy ===");
for (const [name, bg] of [
  ["над светлым фоном страницы", hex(PAGE)],
  ["над голубым пятном brand.sky", spotSky],
  ["над голубым пятном brand.mid", spotMid],
]) {
  const navy = over(hex("#1b3360"), 0.82, bg);
  const rgb = navy.map(Math.round);
  console.log(`\nnavy-стекло ${name}: rgb(${rgb})`);
  console.log(`  #ffffff        ${ratio([255, 255, 255], rgb).toFixed(2)}:1`);
  console.log(`  white/70       ${ratio(over([255, 255, 255], 0.7, rgb), rgb).toFixed(2)}:1`);
  console.log(`  accent2 #2fbaec (кнопка) ${ratio(hex("#2fbaec"), rgb).toFixed(2)}:1`);
}

// ---- подложка модалки и тултип ----
console.log("\n=== прочее ===");
const overlay = over(hex("#0f1b2d"), 0.35, hex(PAGE));
console.log(`подложка модалки rgba(15,27,45,.35) над фоном: rgb(${overlay.map(Math.round)})`);
const tipSurf = over(WHITE, 1 - (1 - 0.5) * (1 - 0.74), spotMid);
console.log(`стекло тултипа над голубым пятном: rgb(${tipSurf.map(Math.round)})`);
for (const [ink, c] of Object.entries(INK)) {
  console.log(`  ${ink.padEnd(8)} ${ratio(hex(c), tipSurf).toFixed(2)}:1`);
}

// ---- приглушённый белый в шапке (text-white/60, /70) ----
console.log("\n=== приглушённый белый в шапке (худший случай: navy над светлым фоном) ===");
const navyWorst = over(hex("#1b3360"), 0.82, hex(PAGE));
for (const a of [1, 0.8, 0.75, 0.7, 0.6]) {
  const c = over([255, 255, 255], a, navyWorst);
  console.log(`  white/${a}  rgb(${c.map(Math.round)})  ${ratio(c, navyWorst).toFixed(2)}:1`);
}
