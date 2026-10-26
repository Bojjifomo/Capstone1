// Phone-width screenshots of the test copy:  node tests/shoot.js <stage> [port]
const { chromium } = require("playwright");
const path = require("path");

const stage = process.argv[2] || "stage1";
const port = process.argv[3] || "7790";
const base = `http://127.0.0.1:${port}`;
const out = (name) => path.join(__dirname, "..", "docs", "screens", `${stage}-${name}.png`);
const sid = (n) => "00000000-0000-0" + String(n).repeat(3) + "-" + String(n).repeat(4) + "-" + String(n).repeat(12);
const wait = (page, ms) => page.waitForTimeout(ms);

async function closeSheet(page) {
  await page.keyboard.press("Escape");
  await wait(page, 300);
}

const SCENES = {
  async stage1(page) {
    await page.screenshot({ path: out("hall"), fullPage: true });
    await page.click(`[data-sid="${sid(4)}"]`);
    await wait(page, 900);
    await page.screenshot({ path: out("sheet-report") });
    await closeSheet(page);
  },
  async stage2(page) {
    await page.click(`[data-sid="${sid(4)}"]`);
    await wait(page, 800);
    await page.fill("#say textarea", "slow: give me a status report, with the numbers");
    await page.click("#say [type=submit]");
    await wait(page, 1600);
    await page.screenshot({ path: out("streaming") });
    await wait(page, 4000);
    await closeSheet(page);
    await page.click(`[data-sid="${sid(1)}"]`);
    await wait(page, 800);
    await page.fill("#say textarea", "When the tests pass, open a PR.");
    await page.click("#say [type=submit]");
    await wait(page, 900);
    await page.screenshot({ path: out("queued") });
    await closeSheet(page);
    await page.click("#post-quest");
    await wait(page, 900);
    await page.screenshot({ path: out("new-job") });
    await closeSheet(page);
    await page.click("[data-open=settings]");
    await wait(page, 700);
    await page.screenshot({ path: out("ravens") });
    await closeSheet(page);
  },
};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("dialog", (d) => d.accept());
  await page.goto(base + "/");
  await page.waitForSelector(".spot");
  await wait(page, 600);
  for (const name of stage.split(",")) await SCENES[name](page);
  await page.goto(base + "/");
  await page.waitForSelector(".spot");
  await page.screenshot({ path: out("hall"), fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  console.log(JSON.stringify({ errors, horizontalOverflow: overflow }));
  await browser.close();
})();
