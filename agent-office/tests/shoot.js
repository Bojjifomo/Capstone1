// Phone-width screenshots of the test copy: node tests/shoot.js <stage> [port]
const { chromium } = require("playwright");
const path = require("path");

const stage = process.argv[2] || "stage";
const port = process.argv[3] || "7790";
const base = `http://127.0.0.1:${port}`;
const out = (name) => path.join(__dirname, "..", "docs", "screens", `${stage}-${name}.png`);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(base + "/");
  await page.waitForSelector(".spot");
  await page.waitForTimeout(600);
  await page.screenshot({ path: out("hall"), fullPage: true });

  const shots = (process.argv[4] || "").split(";").filter(Boolean);
  for (const step of shots) {
    const cut = step.indexOf("=");
    const name = step.slice(0, cut), selector = step.slice(cut + 1);
    await page.click(selector);
    await page.waitForTimeout(900);
    await page.screenshot({ path: out(name) });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
  }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  console.log(JSON.stringify({ errors, horizontalOverflow: overflow }));
  await browser.close();
})();
