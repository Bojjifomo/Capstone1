// Phone-width screenshots of the test copy:  node tests/shoot.js <stage> [port]
const { chromium } = require("playwright");
const path = require("path");

const stage = process.argv[2] || "stage1";
const port = process.argv[3] || "7790";
const base = `http://127.0.0.1:${port}`;
let scene = stage;
const out = (name) => path.join(__dirname, "..", "docs", "screens", `${scene}-${name}.png`);
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
  async stage3(page) {
    const recruits = [
      ["Lyra", "DeFi researcher", "Track LP fee yields on Robinhood Chain and explain them plainly."],
      ["Lyra", "Python backend engineer", "Write tests first. Keep functions small."],
      ["", "Technical writer", "Plain English, short sentences."],
      ["Thorne", "Security reviewer", "Look for injection, auth and secrets mistakes."],
    ];
    for (const [name, role, instructions] of recruits) {
      await page.click("[data-open=hire]");
      await wait(page, 500);
      await page.fill("#hire-form [name=name]", name);
      await page.fill("#hire-form [name=role]", role);
      await page.fill("#hire-form [name=instructions]", instructions);
      await page.click("#hire-form [type=submit]");
      await wait(page, 900);
      if (role.startsWith("Python")) await page.screenshot({ path: out("staff") });
      await closeSheet(page);
    }
    await page.click("[data-staff]");
    await wait(page, 500);
    await page.fill("#errand-form textarea", "slow: summarize this week's best LP pools");
    await page.click("#errand-form [type=submit]");
    await wait(page, 1500);
    await closeSheet(page);
    await page.evaluate(() => document.querySelector("#wings").scrollIntoView());
    await wait(page, 300);
    await page.screenshot({ path: out("wings") });
    await page.click("#post-quest");
    await wait(page, 700);
    await page.evaluate(() => document.querySelector("#sheet-body").scrollTo(0, 9999));
    await page.screenshot({ path: out("party-leader") });
    await closeSheet(page);
  },
  async stage4(page) {
    await page.click("[data-open=disk]");
    await wait(page, 2500);
    await page.click("[data-panel-act=rescan]").catch(() => {});
    await wait(page, 4500);
    await page.screenshot({ path: out("vault") });
    await closeSheet(page);
    for (const name of ["usage", "bots", "ledger"]) {
      await page.click(`.toolbar [data-open=${name}]`);
      await wait(page, 900);
      await page.screenshot({ path: out(name) });
      await closeSheet(page);
    }
    await page.click(".toolbar [data-open=search]");
    await wait(page, 400);
    await page.fill("#search-form input", "endpoints");
    await page.click("#search-form [type=submit]");
    await wait(page, 900);
    await page.screenshot({ path: out("scrying") });
    await closeSheet(page);
  },
  async petitions(page) {
    const plan = "## Problem\nYou find out late when a starred wallet opens a position.\n\n## Change\n1. `worker.py`: after each sync, diff LP events for Watchlist wallets.\n2. `alerts.py` (new): push via the Guild Hall ravens.\n3. Settings toggle **Watchlist alerts**.\n\n## Testing\nUnit tests with recorded events; no extra RPC calls.\n\n## Risk\nNone for RPC quota: reuses events already fetched.";
    await page.evaluate(async (plan) => {
      const post = (body) => fetch("/api/petitions", { method: "POST", headers: { "Content-Type": "application/json", "X-Agent-Office": "1" }, body: JSON.stringify(body) });
      await post({ by: "Orin", title: "Ping me when a Watchlist wallet opens a position", app: "RH Wallet Finder", summary: "Push alert within 5 minutes of a starred wallet opening, adding to or closing an LP position.", impact: "high", effort: "M", plan });
      await post({ by: "Orin", title: "Shareable wallet links on the LP website", app: "LP website", summary: "Open a wallet straight from a link like …/#0x5784.", impact: "medium", effort: "S", plan: "Small change in web/app.js." });
    }, plan);
    await page.reload();
    await page.waitForSelector(".spot");
    await wait(page, 500);
    await page.screenshot({ path: out("toolbar") });
    await page.click("#petitions-btn");
    await wait(page, 900);
    await page.evaluate(() => document.querySelector(".petition details").open = true);
    await wait(page, 300);
    await page.screenshot({ path: out("panel") });
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
  for (const name of stage.split(",")) { scene = name; await SCENES[name](page); }
  await page.goto(base + "/");
  await page.waitForSelector(".spot");
  await page.screenshot({ path: out("hall"), fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  console.log(JSON.stringify({ errors, horizontalOverflow: overflow }));
  await browser.close();
})();
