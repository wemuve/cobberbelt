/**
 * Browser journey test against a running stack (API + worker + web).
 * Uses ONLY a synthetic fixture PDF. Writes screenshots to $SHOTS (default ./e2e-shots).
 *
 *   WEB=http://localhost:3000 FIXTURE=path/to/synthetic.pdf CHROME=/path/to/chrome node e2e/journey.mjs
 * In CI or environments without a system Chrome, install @sparticuz/chromium + puppeteer-core.
 */
import fs from "node:fs";
import path from "node:path";

const WEB = process.env.WEB ?? "http://localhost:3000";
const SHOTS = process.env.SHOTS ?? "e2e-shots";
const FIXTURE = process.env.FIXTURE;
fs.mkdirSync(SHOTS, { recursive: true });

const puppeteer = (await import("puppeteer-core")).default;
let executablePath = process.env.CHROME;
let args = ["--no-sandbox"];
if (!executablePath) {
  const chromium = (await import("@sparticuz/chromium")).default;
  executablePath = await chromium.executablePath();
  args = chromium.args;
}
const browser = await puppeteer.launch({ executablePath, args, headless: true, defaultViewport: { width: 1440, height: 1000 } });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("dialog", async (d) => {
  if (d.message().startsWith("Name for the new map feature")) await d.accept("Kambove Fictif");
  else if (d.message().startsWith("Your professional conclusion")) await d.accept("Position must be field-verified before any further work.");
  else await d.accept();
});

const step = async (name, fn) => {
  process.stdout.write(`• ${name} … `);
  await fn();
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
  console.log("ok");
};
const clickText = async (text, sel = "button, a") => {
  const handle = await page.waitForFunction(
    (t, s) => [...document.querySelectorAll(s)].find((e) => e.textContent?.trim() === t && !e.disabled),
    { timeout: 20000 }, text, sel);
  await handle.asElement().click();
};
const waitText = (text, timeout = 30000) => page.waitForFunction((t) => document.body.innerText.includes(t), { timeout }, text);
const email = `owner-${Date.now()}@example.com`;

try {
  await step("01-register", async () => {
    await page.goto(`${WEB}/login`, { waitUntil: "networkidle0" });
    await clickText("New organisation");
    await page.type("input[name=full_name]", "Ada Mwansa");
    await page.type("input[name=org]", "Synthetic Exploration Ltd");
    await page.type("input[name=email]", email);
    await page.type("input[name=password]", "Correct-Horse-42-battery");
    await clickText("Create organisation");
    await waitText("What is in your evidence base");
  });

  await step("02-source-and-upload", async () => {
    await page.goto(`${WEB}/documents`, { waitUntil: "networkidle0" });
    await page.type("form:nth-of-type(2) input[name=name]", "Synthetic fixture set");
    await page.select("select[name=access_status]", "public_domain");
    await clickText("Register source");
    await waitText("Source registered.");
    const input = await page.waitForSelector("input[type=file]");
    await input.uploadFile(FIXTURE);
    await page.type("input[name=title]", "SYNTHETIC Kambove prospecting report");
    await page.type("input[name=publication_year]", "1962");
    await page.type("input[name=country]", "CD");
    await clickText("Upload and process");
    await waitText("Processing has started");
    await page.waitForFunction(() => document.body.innerText.includes("processed"), { timeout: 60000 });
  });

  await step("03-evidence-viewer", async () => {
    await clickText("SYNTHETIC Kambove prospecting report", "a");
    await waitText("Claims on page 1");
    await page.waitForSelector(".evidence figure img", { timeout: 30000 });
    await page.waitForSelector(".source-text mark");
  });

  await step("04-accept-and-map", async () => {
    // Accept the first coordinate claim on page 1, then promote it to a map feature.
    await page.evaluate(() => {
      const row = [...document.querySelectorAll("tbody tr")].find((r) => r.innerText.startsWith("Coordinates"));
      [...row.querySelectorAll("button")].find((b) => b.textContent === "Accept").click();
    });
    await page.waitForFunction(() => [...document.querySelectorAll("tbody tr")].some((r) => r.innerText.startsWith("Coordinates") && r.innerText.includes("accepted")));
    await clickText("Put on map");
    await waitText("Feature “Kambove Fictif” created");
  });

  await step("05-search", async () => {
    await page.goto(`${WEB}/search`, { waitUntil: "networkidle0" });
    await page.type("input[name=q]", "cuivre");
    await clickText("Search");
    await waitText("Query expanded to");
    await waitText("Kambove Fictif");
  });

  await step("06-map", async () => {
    await page.goto(`${WEB}/map`, { waitUntil: "networkidle0" });
    await page.waitForSelector(".maplibregl-canvas", { timeout: 30000 });
    await new Promise((r) => setTimeout(r, 2500));
    await clickText("Kambove Fictif", "a, td, button").catch(() => {});
  });

  await step("07-project-and-assessment", async () => {
    await page.goto(`${WEB}/projects`, { waitUntil: "networkidle0" });
    await page.type("input[name=name]", "Kambove screening");
    await page.select("select[name=country]", "CD");
    await page.type("textarea[name=description]", "Decide whether the historical occurrence justifies a field visit.");
    await clickText("Create project");
    await clickText("Kambove screening", "a");
    await waitText("Evidence quality");
    await page.type("form input[name=name]", "Licence area (synthetic)");
    for (const [k, v] of [["west", "26.5"], ["south", "-10.9"], ["east", "26.7"], ["north", "-10.7"]]) await page.type(`input[name=${k}]`, v);
    await clickText("Add area");
    await waitText("Area added.");
    await waitText("Kambove Fictif");
    await page.type("input[name=title][minlength='3'][required]:not([placeholder])", "Occurrence position uncertain").catch(() => {});
    await page.type("input[placeholder='Screening assessment']", "Screening assessment v1");
    await clickText("Create assessment");
    await waitText("Assessment created from the current dossier.");
    await clickText("Send for review");
    await waitText("Sent for review.");
  });

  await step("08-organisation", async () => {
    await page.goto(`${WEB}/admin`, { waitUntil: "networkidle0" });
    await waitText("External connections");
    await waitText("document.processed");
  });

  if (errors.length) throw new Error("Page errors:\n" + errors.join("\n"));
  console.log("JOURNEY OK");
} catch (e) {
  await page.screenshot({ path: path.join(SHOTS, "zz-failure.png"), fullPage: true });
  console.error("\nFAILED:", e.message, "\nPage errors:", errors);
  process.exitCode = 1;
} finally {
  await browser.close();
}
