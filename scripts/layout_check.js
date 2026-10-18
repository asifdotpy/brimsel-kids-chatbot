/* Verify the two layout bugs a real user reported, by measuring the DOM.
 *
 *   1. "In my small laptop screen the UI seems too much zoomed in. I have to
 *      zoom out to 67%."  -> at 1366x768 and 100% zoom, the whole app must fit
 *      the viewport with no page scrolling.
 *   2. "If continuously chatting the scroller grows but the input box go down
 *      and needs to scroll down to write new messages."  -> after many turns the
 *      composer must still be fully inside the viewport, and the ONLY scrolling
 *      element must be the message list.
 *
 * Geometry is authoritative here: this headless Chromium intermittently
 * substitutes one emoji glyph for all emoji, which is a font/render race and not
 * an app bug.
 */

/* Puppeteer is not a project dependency: this is a developer check, not part of
 * the test suite (which is deliberately offline and dependency-light). Resolve it
 * from an explicit PUPPETEER path, then from an npx cache, then from node_modules.
 */
const fs = require("fs");
const os = require("os");
const path = require("path");

function loadPuppeteer() {
  const candidates = [process.env.PUPPETEER, "puppeteer"];
  const npxRoot = path.join(os.homedir(), ".npm", "_npx");
  if (fs.existsSync(npxRoot)) {
    for (const entry of fs.readdirSync(npxRoot)) {
      candidates.push(path.join(npxRoot, entry, "node_modules", "puppeteer"));
    }
  }
  for (const candidate of candidates) {
    if (!candidate) continue;
    try { return require(candidate); } catch (err) { /* try the next one */ }
  }
  console.error(
    "Could not load puppeteer.\n" +
    "Install it (npm i -D puppeteer) or point PUPPETEER at an existing copy:\n" +
    "  PUPPETEER=/path/to/node_modules/puppeteer node scripts/layout_check.js"
  );
  process.exit(2);
}

const puppeteer = loadPuppeteer();

const BASE = process.env.BASE || "http://127.0.0.1:8099";
const VIEWPORTS = [
  { name: "laptop  1366x768", width: 1366, height: 768 },
  { name: "small   1280x720", width: 1280, height: 720 },
  { name: "phone    390x844", width: 390, height: 844 },
];

function ok(condition) {
  return condition ? "PASS" : "FAIL";
}

(async () => {
  const browser = await puppeteer.launch({
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
    defaultViewport: null,
  });

  let failures = 0;
  const fail = (message) => { failures += 1; console.log(`    FAIL  ${message}`); };

  for (const viewport of VIEWPORTS) {
    const page = await browser.newPage();
    await page.setViewport({ width: viewport.width, height: viewport.height });
    const errors = [];
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(String(e)));

    await page.goto(BASE, { waitUntil: "networkidle2" });
    await new Promise((r) => setTimeout(r, 900));

    console.log(`\n=== ${viewport.name} ===`);

    const measure = () => page.evaluate(() => {
      const composer = document.querySelector(".composer").getBoundingClientRect();
      const chat = document.querySelector(".chat");
      const footer = document.querySelector(".footer").getBoundingClientRect();
      return {
        innerHeight: window.innerHeight,
        bodyScroll: document.body.scrollHeight,
        htmlScroll: document.documentElement.scrollHeight,
        bodyOverflow: getComputedStyle(document.body).overflowY,
        composerTop: Math.round(composer.top),
        composerBottom: Math.round(composer.bottom),
        composerHeight: Math.round(composer.height),
        quickHeight: Math.round(document.querySelector(".quick-row").getBoundingClientRect().height),
        chatClient: chat.clientHeight,
        chatScroll: chat.scrollHeight,
        scrollableOutsideChat: document.documentElement.scrollHeight > window.innerHeight + 1,
        footerBottom: Math.round(footer.bottom),
        fontPx: getComputedStyle(document.body).fontSize,
        tapSize: getComputedStyle(document.documentElement).getPropertyValue("--tap").trim(),
      };
    });

    let m = await measure();
    console.log(`  body font ${m.fontPx} | --tap ${m.tapSize} | viewport height ${m.innerHeight}`);
    if (m.scrollableOutsideChat) {
      fail(`the page itself scrolls (${m.htmlScroll} > ${m.innerHeight}) - the composer gets pushed off`);
    }
    if (m.bodyOverflow !== "hidden") fail(`body overflow-y is ${m.bodyOverflow}, expected hidden`);
    if (m.composerBottom > m.innerHeight) {
      fail(`composer bottom ${m.composerBottom} is below the fold (${m.innerHeight})`);
    }
    if (m.composerHeight < 44) fail(`composer is only ${m.composerHeight}px tall`);
    if (m.quickHeight < 38) {
      fail(`the quick-say row is crushed to ${m.quickHeight}px; only the message list may flex`);
    }
    console.log(`  composer: ${m.composerTop}..${m.composerBottom}  (visible: ${ok(m.composerBottom <= m.innerHeight)})`);
    console.log(`  quick-say row height: ${m.quickHeight}px`);

    // Now the reported bug: keep chatting and check the composer stays put.
    await page.evaluate(async () => {
      const chat = document.querySelector(".chat");
      for (let i = 0; i < 30; i += 1) {
        const child = document.createElement("div");
        child.className = "bubble child";
        child.textContent = `This is message number ${i} and it is fairly long so it wraps onto lines.`;
        chat.appendChild(child);
        const buddy = document.createElement("div");
        buddy.className = "bubble buddy";
        buddy.textContent = `Great job! Can you say word number ${i}? You are doing so well today.`;
        chat.appendChild(buddy);
      }
      chat.scrollTop = chat.scrollHeight;
    });
    await new Promise((r) => setTimeout(r, 300));

    const after = await measure();
    console.log(`  after 60 bubbles: chat scrolls internally ${ok(after.chatScroll > after.chatClient)} `
      + `(${after.chatScroll} > ${after.chatClient})`);
    if (after.scrollableOutsideChat) fail("the page scrolls after a long conversation");
    if (after.composerBottom > after.innerHeight) {
      fail(`composer pushed off screen after 60 bubbles (bottom ${after.composerBottom} > ${after.innerHeight})`);
    }
    if (!(after.chatScroll > after.chatClient)) {
      fail("the message list is not the scroll container; long chats would grow the page");
    }
    console.log(`  composer after 60 bubbles: bottom ${after.composerBottom} of ${after.innerHeight} `
      + `(visible: ${ok(after.composerBottom <= after.innerHeight)})`);

    // The level switcher must exist and be reachable.
    const levelButtons = await page.$$eval("#levelSeg .seg-btn", (nodes) =>
      nodes.map((n) => ({ label: n.textContent.trim(), pressed: n.getAttribute("aria-pressed") })));
    console.log(`  level control: ${levelButtons.map((b) => `${b.label}${b.pressed === "true" ? "*" : ""}`).join(" ")}`);
    if (levelButtons.length !== 3) fail("the level switcher is missing or incomplete");

    // The settings panel must open without breaking the layout.
    await page.click("#settingsToggle");
    await new Promise((r) => setTimeout(r, 300));
    const settings = await page.evaluate(() => {
      const panel = document.querySelector("#settingsPanel");
      const rect = panel.getBoundingClientRect();
      return {
        visible: !panel.hidden,
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        hasJsonDump: /^\s*\{/.test(document.querySelector("#debugOut").textContent.trim()),
        hasSummary: !!document.querySelector("#settingsPanel summary"),
      };
    });
    console.log(`  settings: open=${settings.visible} size=${settings.width}x${settings.height} `
      + `raw-json-behind-disclosure=${ok(settings.hasSummary)}`);
    if (!settings.visible) fail("the settings panel did not open");
    if (settings.width < 240) fail(`settings panel is too narrow (${settings.width}px)`);

    const stillFits = await page.evaluate(() =>
      document.documentElement.scrollHeight <= window.innerHeight + 1);
    if (!stillFits) fail("opening settings made the page scroll");
    await page.click("#settingsClose");

    if (errors.length) {
      fail(`console/page errors: ${errors.slice(0, 3).join(" | ")}`);
    } else {
      console.log("  console errors: none");
    }

    if (viewport.width === 1366) {
      await page.screenshot({ path: "/tmp/buddy-1366.png", fullPage: false });
      await page.click("#settingsToggle");
      await new Promise((r) => setTimeout(r, 250));
      await page.screenshot({ path: "/tmp/buddy-1366-settings.png", fullPage: false });
    }
    if (viewport.width === 390) {
      await page.screenshot({ path: "/tmp/buddy-390.png", fullPage: false });
    }

    await page.close();
  }

  await browser.close();
  console.log(`\n${failures === 0 ? "ALL LAYOUT CHECKS PASSED" : `${failures} LAYOUT CHECK(S) FAILED`}`);
  process.exit(failures === 0 ? 0 : 1);
})();
