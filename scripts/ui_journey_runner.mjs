// Workbench UX journey runner (2026-10-27_workbench-ux-iteration). Ported from
// the review-session driver /tmp/ux-journey.mjs with three deltas so the journey
// is re-runnable for before/after evidence (evidence/baseline → evidence/after):
// - output directory comes from the plan (plan.out) instead of a hardcoded /tmp;
// - the bad-token step keeps its reload (with U1 fixed the gate now self-recovers,
//   the reload stays only to keep the step boundaries identical to baseline);
// - exits non-zero when page errors are collected (baseline run printed them and
//   exited 0; a committed harness must be usable as a regression gate).
//
// Receives a plan JSON: {origin, bootstrap_url, out, requireBase}. Bootstrap
// tokens are one-use capabilities: they live only in the plan temp file and are
// never printed (same rule as ui_shots_runner.mjs).

import { createRequire } from "node:module";
import { mkdirSync, readFileSync } from "node:fs";

const plan = JSON.parse(readFileSync(process.argv[2], "utf8"));
const require = createRequire(plan.requireBase);
const { chromium } = require("@playwright/test");

mkdirSync(plan.out, { recursive: true });
const shot = async (page, name, fullPage = false) => {
  await page.waitForTimeout(350);
  await page.screenshot({ path: `${plan.out}/${name}.png`, fullPage });
  console.log(`shot: ${name}`);
};

// page.reload 偶发默认 30s TimeoutError（2026-10-27 复审实跑：第 1 次 step 10 超时
// 未捕获整轮崩溃，同命令第 2 次全绿）——demo 链在途请求偶发拖住文档加载。重试一次
// （放宽到 60s）再失败才向上抛，门禁出证据不再依赖人工失败重跑。
const reloadWithRetry = async (page, label) => {
  try {
    await page.reload({ waitUntil: "domcontentloaded" });
  } catch (cause) {
    console.log(`reload retry (${label}): ${String(cause).split("\n")[0]}`);
    await page.reload({ waitUntil: "domcontentloaded", timeout: 60_000 });
  }
};

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  acceptDownloads: true,
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));

// 1. gate 冷启动
await page.goto(`${plan.origin}/?s=1`, { waitUntil: "domcontentloaded" });
await shot(page, "01-gate-cold");

// 2. gate 错误口令（U1 对照：修复后应就地报错并还原表单）
await page.getByPlaceholder("粘贴一键链接或口令").fill("wrong-token-abc");
await page.getByRole("button", { name: "进入" }).click();
await page.waitForTimeout(700);
await shot(page, "02-gate-bad-token");

// 坏口令步骤后 reload——U1 已修后 gate 本可就地重试，保留 reload 使步骤边界与
// baseline 逐张可比（findings.md 的 02 截图对照口径不变）。
await reloadWithRetry(page, "step2");
await page.waitForTimeout(500);

// 3. 正确口令（粘贴完整链接——真实用户从日志/文件复制的形态）
await page.getByPlaceholder("粘贴一键链接或口令").fill(plan.bootstrap_url);
await page.getByRole("button", { name: "进入" }).click();
await page.locator(".brand strong").waitFor({ timeout: 20000 });
await shot(page, "03-entered-welcome");

// 4. 示例任务按钮 → 草稿填入
await page.getByText("检查样例仓库").click();
await shot(page, "04-example-draft");

// 5. 发送 → 流式中
await page.locator(".send-button").click();
await page.waitForTimeout(1800);
await shot(page, "05-streaming-early");
await page.waitForTimeout(2500);
await shot(page, "06-streaming-mid");

// 6. 审批卡出现
await page.locator(".approval-card").first().waitFor({ timeout: 60000 });
await shot(page, "07-approval-card");
// 批准按钮连点保护
for (let i = 0; i < 8; i++) {
  try {
    await page.getByRole("button", { name: "批准" }).first().click({ timeout: 2500 });
    break;
  } catch { /* re-render race */ }
}

// 7. 完成态：对话+检查器
await page.getByText(/turn 完成/).first().waitFor({ timeout: 120000 });
await shot(page, "08-done-chat-inspector");

// 8. 工具详情（点第一个工具行）
const toolRow = page.locator(".tool-row").first();
if (await toolRow.isVisible().catch(() => false)) {
  await toolRow.click();
  await shot(page, "09-tool-detail");
}

// 9. 结果 tab：运行报告
await page.getByRole("tab", { name: /结果/ }).click();
await page.waitForTimeout(400);
await shot(page, "10-report-panel");
// 导出报告（捕获下载是否真的发生）
const downloadPromise = page.waitForEvent("download", { timeout: 8000 }).then(() => "downloaded").catch(() => "no-download");
await page.getByRole("button", { name: /导出运行报告/ }).click();
console.log(`export: ${await downloadPromise}`);

// 10. 刷新恢复（真实用户 F5；U2 对照：修复后 composer 应回到可写态）
await reloadWithRetry(page, "step10-reload-restore");
await page.waitForTimeout(1200);
await shot(page, "11-reload-restore");

// 11. 新建会话 → 空态
await page.getByRole("button", { name: /新建会话/ }).first().click();
await page.waitForTimeout(500);
await shot(page, "12-new-session-sidebar");

// 12. 侧栏回放旧会话（REPL 会话区若有）
const old = page.locator(".session-item").first();
if (await old.isVisible().catch(() => false)) {
  await old.click();
  await page.waitForTimeout(1000);
  await shot(page, "13-sidebar-reswitch");
}

// 13. 错误路径：不存在的线程（U3 对照：修复后应有显著错误条）
await page.goto(`${plan.origin}/?s=2#/threads/thread_nonexistent_00`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
await shot(page, "14-nonexistent-thread");

// 14. 畸形 hash
await page.goto(`${plan.origin}/?s=3#/threads/%zz`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
await shot(page, "15-malformed-hash");

// 15. 任务面板 / 用量面板（U7 对照：副标题应为用户文案）
await page.goto(`${plan.origin}/?s=4#/tasks`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1000);
await shot(page, "16-tasks-panel");
await page.goto(`${plan.origin}/?s=5#/usage`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
await shot(page, "17-usage-panel");

// 16. 响应式：900 / 500
await page.setViewportSize({ width: 900, height: 900 });
await page.goto(`${plan.origin}/?s=6#/tasks`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(800);
await shot(page, "18-tablet-900");
await page.setViewportSize({ width: 500, height: 900 });
await page.goto(`${plan.origin}/?s=7`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(800);
await shot(page, "19-mobile-500");

console.log(`pageerrors: ${errors.length ? errors.join(" | ") : "none"}`);
await browser.close();
if (errors.length > 0) {
  process.exit(1);
}
