// Pretend to be an iOS home-screen app that reports width 0 for the first moments after launch.
// Run against the test copy: sh tests/run_copy.sh, then node tests/ios_launch.js (late should be the full width).
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 390, height: 844 } });
  await p.addInitScript(() => {
    const t0 = Date.now();
    for (const prop of ["clientWidth"]) {
      const d = Object.getOwnPropertyDescriptor(Element.prototype, prop);
      Object.defineProperty(Element.prototype, prop, { get() { return Date.now() - t0 < 1500 ? 0 : d.get.call(this); } });
    }
  });
  await p.goto("http://127.0.0.1:7790/");
  await p.waitForTimeout(600);
  const early = await p.$eval("#town-stage", (e) => e.getBoundingClientRect().width);
  await p.waitForTimeout(2500);
  const late = await p.$eval("#town-stage", (e) => e.getBoundingClientRect().width);
  console.log(JSON.stringify({ early, late }));
  await b.close();
})();
