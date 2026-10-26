// Playwright screenshot runner for scripts/ui_compare_shots.py (task_0002/task_0005,
// 2026-10-26_workbench-kitagent-ui). Receives a plan JSON: {out, side, requireBase,
// sides: {nexuscli|kitagent: {origin, bootstrap_url}}}.
//
// Per side (each in a fresh browser context):
// - nexuscli: gate page (no fragment) → bootstrap URL (cookie set) → main view →
//   create thread + post one demo turn through the API (header channel, csrf-free)
//   → thread replay / tasks / usage screenshots.
// - kitagent: onboarding → 新建会话 welcome → send the example task → approval card
//   screenshot → allow once → run report screenshot.
//
// Bootstrap tokens are one-use capabilities: they live only in the plan temp file
// and are never printed.

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const plan = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(plan.requireBase);
const { chromium } = require("@playwright/test");

const VIEWPORT = { width: 1440, height: 900 };
const out = (name) => `${plan.out}/${name}.png`;

async function shot(page, name, fullPage = true) {
  await page.waitForTimeout(400);
  await page.screenshot({ path: out(name), fullPage });
  console.log(`shot: ${name}`);
}

async function nexuscli(info) {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page = await context.newPage();

  // Distinct URLs force full document loads: a fragment-only change would not
  // re-run the app's bootstrap effect.
  await page.goto(`${info.origin}/?shot=gate`, { waitUntil: "domcontentloaded" });
  await shot(page, "nexuscli-gate", false);

  await page.goto(info.bootstrap_url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(800);
  await shot(page, "nexuscli-main");

  // Drive the real UI path: composer → demo turn → write_file approval suspends
  // the run (A9) → approve in the web UI → turn completes.
  await page.locator("textarea").first().fill("检查样例 Python 仓库，执行检查脚本并生成检查报告。");
  await page.getByRole("button", { name: /发送/ }).click();
  await page.locator(".approval-card, .approval-panel").first().waitFor({ timeout: 60000 });
  await shot(page, "nexuscli-approval", false);
  for (let attempt = 0; attempt < 8; attempt += 1) {
    try {
      await page.getByRole("button", { name: /批准|允许/ }).first().click({ timeout: 2500 });
      break;
    } catch {
      /* card re-render race; retry with a fresh locator */
    }
  }
  await page.getByText(/turn 完成|本轮运行报告/).first().waitFor({ timeout: 120000 });
  await shot(page, "nexuscli-thread");

  await page.goto(`${info.origin}/?shot=tasks#/tasks`, { waitUntil: "domcontentloaded" });
  await shot(page, "nexuscli-tasks", false);
  await page.goto(`${info.origin}/?shot=usage#/usage`, { waitUntil: "domcontentloaded" });
  await shot(page, "nexuscli-usage", false);

  await browser.close();
}

async function kitagent(info) {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: VIEWPORT });
  const page = await context.newPage();

  await page.goto(info.bootstrap_url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(600);
  await shot(page, "kitagent-onboarding", false);

  await page.getByRole("button", { name: /新建会话/ }).first().click();
  await page.waitForTimeout(600);
  await shot(page, "kitagent-chat-welcome", false);

  await page.getByRole("textbox", { name: /输入任务/ })
    .fill("检查样例 Python 仓库，执行检查脚本并生成检查报告。");
  await page.keyboard.press("Enter");
  // The demo chain suspends more than once (bash + write_file); approve through
  // every card until the run report lands in the inspector.
  for (let i = 0; i < 6; i += 1) {
    const approval = page.getByText("需要你的确认").first();
    const report = page.getByText("本轮运行报告").first();
    const seen = await Promise.race([
      approval.waitFor({ timeout: 30000 }).then(() => "approval"),
      report.waitFor({ timeout: 30000 }).then(() => "report"),
    ]).catch(() => null);
    if (seen === "report") break;
    if (seen === "approval") {
      if (i === 0) await shot(page, "kitagent-approval", false);
      // The card re-renders on every state push (element detaches mid-click);
      // retry with fresh locators until the decision lands.
      for (let attempt = 0; attempt < 8; attempt += 1) {
        try {
          await page
            .getByRole("button", { name: /允许一次|允许并记住/ })
            .first()
            .click({ timeout: 2500 });
          break;
        } catch {
          /* detached again; retry */
        }
      }
      continue;
    }
    break;
  }
  await page.waitForTimeout(800);
  await shot(page, "kitagent-run-done");
  const resultTab = page.getByRole("tab", { name: /结果/ });
  if (await resultTab.isVisible().catch(() => false)) {
    await resultTab.click();
    await page.waitForTimeout(400);
    await shot(page, "kitagent-report", false);
  }

  await browser.close();
}

if (plan.sides.nexuscli && plan.side !== "kitagent") await nexuscli(plan.sides.nexuscli);
if (plan.sides.kitagent && plan.side !== "nexuscli") await kitagent(plan.sides.kitagent);
console.log("done");
