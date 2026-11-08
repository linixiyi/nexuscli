// workbench.spec.ts：Playwright e2e 基线（A12，用例编排 Ported from kitagent (MIT,
// zed123214/kitagent) frontend/e2e/workbench.spec.ts，服务与断言适配 NexusCLI Runtime API）。
//
// 基线用例（serial 模式，前序失败则后续跳过）：
//   1. bootstrap 认证流：#bootstrap= 一次性口令 → 片段抹除 → 门通过（并产生后续用例
//      复用的已认证页面）
//   2. 加载：静态托管 + React 挂载 + 无页面错误
//   2.5 gate 粘贴换发（独立服务实例）：页内换发 → 可发任务（复审 F2 回归）
//   2.6 U1 回归（独立服务实例）：坏口令就地报错 + 表单可重试（2026-10-27 UX 实测）
//   2.7 U2 回归：刷新后 csrf 自动重签恢复可写（2026-10-27 UX 实测）
//   3. 发起 turn：x-api-key 头通道（csrf 豁免，api.py:262-272）建线程 + 幂等 POST
//      turn（x-request-id 重放同回执）+ 事件 seq 严格连续（state.replayEvents 折叠
//      不抛 SequenceGap）
//   4. 会话列表：侧栏渲染工作会话/会话回放两组列表（2026-10-26 UI 重构后侧栏承载）
//   5. 任务/用量面板：副标题用户文案（U7 断言同步，2026-10-27 UX 实测）
//   6. W1 慢等待提示：阈值经 localStorage 键 nexuscli:slow-hint-ms 注入（2026-10-27
//      workbench-ux-round2；ChatPanel.tsx 顶部注释），UI 发 turn 后 3s 内出现提示
//   7. U6 报告证据：结果 tab「执行事实与来源」逐工具行，点击跳执行详情
//   8. W2 会话回放：植入 title 为空的 REPL 会话 → 条目显示「会话 YYYY-MM-DD HH:MM」，
//      不再出现 "(untitled)" 裸文案（植入手段见 seedUntitledSession）
//
// 服务策略：起真服务 scripts/web_validation_server.py（A11/lane2 交付）。脚本或浏览器
// 二进制缺失时：本地整文件跳过并留说明（e2e 是本地可选步骤），CI（env CI 置位）必须
// 硬失败——CI frontend job 先装 Chromium 再跑，缺失即环境错配，不允许静默变绿
// （task_0020）。turn 用例依赖离线 Demo provider（无 key 端到端）：服务报
// 「NEXUSCLI_API_KEY is not configured」时按未就绪跳过，不伪造通过。
//
// 2026-10-26 UI 重构（workbench-kitagent-ui）：App 已接入 useWorkbench 的
// probeAuthorized 探针——带有效 cookie 的冷加载直接进工作台（用例 1 的页面继续
// 被用例 4/5 复用）；无 cookie 冷加载显示认证门（用例 2），门上有粘贴换发入口。

import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";

import { replayEvents } from "../src/state";

test.describe.configure({ mode: "serial" });

test.setTimeout(90_000);

const repoRoot = path.resolve(import.meta.dirname, "..", "..");
const validationServer = path.join(repoRoot, "scripts", "web_validation_server.py");
// lane2 交付的 validation server 以 api_key="offline-demo" 构造 RuntimeApiServer
// （脚本内硬编码，忽略外部 env）；保留 env 覆盖缝以便脚本演进。
const apiKey = process.env.NEXUSCLI_E2E_API_KEY ?? "offline-demo";
// task_0020：CI（GitHub Actions 置位 CI=true）中 e2e 不允许静默跳过——浏览器缺失、
// validation server 未就绪、静态托管缺 dist 都必须硬失败；本地保留 skip 语义。
const inCI = process.env.CI === "true" || process.env.CI === "1";

let serverProcess: ChildProcess | null = null;
let harnessReady = false;
let servesWorkbench = false;
let skipReason =
  `e2e 基线跳过：scripts/web_validation_server.py 尚未就绪（A11/lane2 并行交付）。` +
  `就绪后重跑 npm run test:e2e。`;
// validation server 未就绪的原始原因（CI 硬失败时嵌入错误信息，本地进 skipReason）
let harnessFailure = "";
let uiSkipReason = "";
let origin = "";
let bootstrapToken = "";
// 一次性口令只能换发一次 → 已认证页面只能由用例 1 产出，后续 UI 用例串行复用
// （storageState 方案在本仓不可行：App 无 cookie 探针，冷加载恒为引导门）。
let authedContext: BrowserContext | null = null;
let authedPage: Page | null = null;
// 自管浏览器实例（task_0020）：本 spec 不消费 browser/page 内建 fixture——内建
// fixture 在测试体第一行 test.skip() 执行**之前**就绪并 launch，浏览器缺失时会把
// 「本地可 skip」打回 fixture 硬失败；自管实例让 beforeAll 的探针判定成为唯一入口，
// 本地缺浏览器 → 整文件如实 skip，CI（env CI 置位）→ 硬失败。
let browserHandle: Browser | null = null;
// W2 用例要在服务端植入一条无标题 REPL 会话。validation server 把 HOME 指到自己的
// tempfile.TemporaryDirectory（scripts/web_validation_server.py:98-101），e2e 拿不到该路径——
// 但 Python tempfile 遵循 TMPDIR：主服务实例用 e2e 自有的 TMPDIR 启动，READY 后按前缀
// nexuscli-web-validation-* 定位其根目录（sessions 在 <root>/.nexuscli/sessions，工作区
// cwd 是 <root>/demo）。定位失败时 W2 用例如实跳过。
let serverTmpDir: string | null = null;
let serverRoot: string | null = null;
// 隔离 HOME 壳目录登记处：beforeAll 的主服务实例与 spawnServer 的独立实例各自
// mkdtempSync 一份，之前从不 rmSync——每跑一次 e2e 就泄漏一个（主 1 个 + 每个独立
// 用例 1 个）。统一登记到这里，afterAll 在服务进程全部停止后一并清理。
let isolatedHomeDirs: string[] = [];

test.beforeAll(async () => {
  if (!existsSync(validationServer)) {
    if (inCI) {
      throw new Error(
        "CI 中 e2e 不允许静默跳过：scripts/web_validation_server.py 缺失（task_0020）。",
      );
    }
    return; // 本地：保持 harnessReady=false，用例按 skipReason 跳过
  }
  // 浏览器二进制缺失 → 如实跳过（不安装、不伪造结果）。
  // 用真实 launch 探针而非 executablePath() 存在性检查：新版 Playwright 的默认
  // headless 走独立的 chrome-headless-shell 二进制，executablePath() 指向完整版
  // Chromium，二者可能只装其一。探针与实例合一：launch 成功即保留自管实例
  // （browserHandle），全文件不再走会抢在 test.skip() 之前 launch 的内建 fixture。
  const { chromium } = await import("@playwright/test");
  try {
    browserHandle = await chromium.launch();
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message.split("\n")[0] : String(cause);
    if (inCI) {
      throw new Error(
        `CI 中 e2e 不允许静默跳过：Playwright 浏览器无法启动（${reason}）。` +
          `CI frontend job 需先 npx playwright install chromium（task_0020）。`,
      );
    }
    skipReason =
      `e2e 基线跳过：Playwright 浏览器无法启动（${reason}）。` +
      `可执行 cd frontend && npx playwright install chromium；e2e 为本地可选步骤。`;
    return;
  }
  // 隔离 HOME/USERPROFILE：runtime DB（~/.nexuscli/runtime/runtime.db，api.py:123）不得写进真实用户目录
  const isolatedHome = mkdtempSync(path.join(tmpdir(), "nexuscli-e2e-"));
  isolatedHomeDirs.push(isolatedHome);
  serverTmpDir = mkdtempSync(path.join(tmpdir(), "nexuscli-e2e-tmpdir-"));
  const pythonArgv = process.env.NEXUSCLI_E2E_PYTHON
    ? [process.env.NEXUSCLI_E2E_PYTHON, validationServer]
    : ["uv", "run", "python", validationServer];
  const command = pythonArgv[0];
  serverProcess = spawn(command, pythonArgv.slice(1), {
    cwd: repoRoot,
    env: {
      ...process.env,
      HOME: isolatedHome,
      USERPROFILE: isolatedHome,
      TMPDIR: serverTmpDir,
      NEXUSCLI_RUNTIME_API_KEY: apiKey,
    },
    stdio: "pipe",
    detached: true, // 独立进程组：uv → python 的整组可被 killpg 清空（AGENTS.md 进程守则）
  });
  const ready = await new Promise<{ origin: string; token: string }>((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(
      () => reject(new Error("validation server 90s 内未就绪（无 READY 行/一键链接输出）")),
      90_000,
    );
    const onData = (chunk: Buffer): void => {
      buffer += String(chunk);
      for (const line of buffer.split("\n")) {
        // 协议一（kitagent 同款）：READY {"origin": ..., "token": ...}
        if (line.startsWith("READY ")) {
          try {
            const payload = JSON.parse(line.slice(6)) as { origin?: string; token?: string };
            if (typeof payload.origin === "string" && typeof payload.token === "string") {
              clearTimeout(timer);
              resolve({ origin: payload.origin, token: payload.token });
              return;
            }
          } catch {
            // 非 JSON 的 READY 行，继续等一键链接兜底
          }
        }
        // 协议二（runtime 自身输出）：一键链接 http://127.0.0.1:<port>/#bootstrap=<token>
        const link = line.match(/http:\/\/127\.0\.0\.1:(\d+)\/#bootstrap=([A-Za-z0-9_-]+)/);
        if (link !== null) {
          clearTimeout(timer);
          resolve({ origin: `http://127.0.0.1:${link[1]}`, token: link[2] });
          return;
        }
      }
    };
    serverProcess?.stdout?.on("data", onData);
    serverProcess?.stderr?.on("data", onData);
    serverProcess?.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`validation server 提前退出（code ${code}）`));
    });
  }).then(
    (value) => value,
    (cause: unknown) => {
      harnessFailure = cause instanceof Error ? cause.message : String(cause);
      skipReason = `e2e 基线跳过：validation server 未就绪（${harnessFailure}）。`;
      return null;
    },
  );
  if (ready === null) {
    await stopServer();
    if (inCI) {
      throw new Error(
        `CI 中 e2e 不允许静默跳过：validation server 未就绪（${harnessFailure}）（task_0020）。`,
      );
    }
    return;
  }
  origin = ready.origin;
  bootstrapToken = ready.token;
  harnessReady = true;
  const rootName = readdirSync(serverTmpDir).find((name) => name.startsWith("nexuscli-web-validation-"));
  serverRoot = rootName === undefined ? null : path.join(serverTmpDir, rootName);
  // 静态托管探测：_workbench_dist_root() 是 cwd 相对解析（api.py:1141-1145），而
  // validation server 把 server.cwd 指到临时 demo 工作区（scripts/web_validation_server.py
  // cwd=str(workspace)）——dist 不在其下时服务端返回「UI 未构建」空态页，/v1 API 不受影响。
  // UI 基线按此如实跳过并留说明；API 基线（发起 turn）照常执行。
  const homeHtml = await fetch(`${origin}/`).then((r) => r.text());
  servesWorkbench = homeHtml.includes('<div id="root">') && !homeHtml.includes("UI 未构建");
  if (!servesWorkbench) {
    if (inCI) {
      await stopServer();
      throw new Error(
        "CI 中 e2e 不允许静默跳过：validation server 未托管已构建的 frontend/dist" +
          "（dist 按 server.cwd 相对解析）。CI frontend job 需在 e2e 前先 npm run build（task_0020）。",
      );
    }
    uiSkipReason =
      "UI 基线跳过：validation server 未托管已构建的 frontend/dist（dist 按 server.cwd 相对解析，" +
      "而脚本把 cwd 指到临时 demo 工作区）——构建同步与 /v1 API 均正常，属 lane 间契约缝隙，待收口任务修复。";
  }
});

test.afterAll(async () => {
  await authedContext?.close();
  await browserHandle?.close();
  await stopServer();
  // 服务端的 TemporaryDirectory 随进程退出自清；外层 TMPDIR 壳目录由本文件创建、本文件清。
  if (serverTmpDir !== null) {
    rmSync(serverTmpDir, { recursive: true, force: true });
  }
  // 隔离 HOME 壳目录同理：主实例与 spawnServer 的独立实例都在这里清（force 吞掉
  // 个别残留错误，不因清理失败掩盖测试结果）。
  for (const dir of isolatedHomeDirs) {
    rmSync(dir, { recursive: true, force: true });
  }
  isolatedHomeDirs = [];
});

async function stopServer(): Promise<void> {
  const proc = serverProcess;
  if (proc === null || proc.pid === undefined) return;
  serverProcess = null;
  if (proc.exitCode !== null) return;
  proc.stdin?.write("\n"); // kitagent 同款：stdin 换行请求退出
  await new Promise<void>((resolve) => {
    const timer = setTimeout(() => resolve(), 5_000);
    proc.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
  if (proc.exitCode === null) {
    try {
      process.kill(-proc.pid, "SIGTERM"); // 清掉 uv→python 整组
    } catch {
      /* 进程组可能已退出 */
    }
    await new Promise<void>((resolve) => {
      const timer = setTimeout(() => resolve(), 3_000);
      proc.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
    });
    if (proc.exitCode === null) {
      try {
        process.kill(-proc.pid, "SIGKILL");
      } catch {
        /* 同上 */
      }
    }
  }
}

test("bootstrap 认证流：一次性口令换发并抹除片段", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  // 跨用例持久页面：Playwright 的 page fixture 每测试隔离、用例结束即销毁——
  // 串行 authedPage 交接必须用自管 browser 实例自建 context（主会话收口修复）。
  // 1440x900：侧栏在 ≥1280px 才常驻（kitagent 同款断点），默认 1280 贴着断线。
  const context = await browserHandle!.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(`${origin}/#bootstrap=${bootstrapToken}`);
  // 换发后口令不得留在地址栏（kitagent 同款断言）
  await expect(page).not.toHaveURL(/bootstrap=/);
  // 门通过：品牌侧栏与对话主面板渲染，认证门不再出现
  await expect(page.locator(".brand strong")).toHaveText("NexusCLI");
  await expect(page.locator(".gate-card")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "新建会话" })).toBeVisible();
  authedContext = page.context();
  authedPage = page;
});

test("加载：静态托管 + React 挂载无页面错误", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  // 独立冷加载 context：无 cookie（内建 page fixture 改自管实例，见 browserHandle 注释）
  const context = await browserHandle!.newContext();
  const page = await context.newPage();
  try {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(origin);
    await expect(page.getByRole("heading", { name: "NexusCLI Workbench" })).toBeVisible();
    // 无 cookie 冷加载 → 认证门（含粘贴换发入口，task_0006），无页面错误。
    await expect(page.locator(".gate-card")).toBeVisible();
    await expect(page.getByPlaceholder("粘贴一键链接或口令")).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await context.close();
  }
});


// 独立 spawn 一台 validation server（粘贴换发用例专用：一次性口令不能与用例 1 共享）。
async function spawnServer(): Promise<{ origin: string; token: string; proc: ChildProcess }> {
  const isolatedHome = mkdtempSync(path.join(tmpdir(), "nexuscli-e2e-paste-"));
  isolatedHomeDirs.push(isolatedHome);
  const pythonArgv = process.env.NEXUSCLI_E2E_PYTHON
    ? [process.env.NEXUSCLI_E2E_PYTHON, validationServer]
    : ["uv", "run", "python", validationServer];
  const proc = spawn(pythonArgv[0], pythonArgv.slice(1), {
    cwd: repoRoot,
    env: {
      ...process.env,
      HOME: isolatedHome,
      USERPROFILE: isolatedHome,
      NEXUSCLI_RUNTIME_API_KEY: apiKey,
    },
    stdio: "pipe",
    detached: true,
  });
  const ready = await new Promise<{ origin: string; token: string }>((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("paste server 90s 内未就绪")), 90_000);
    const onData = (chunk: Buffer): void => {
      buffer += String(chunk);
      for (const line of buffer.split("\n")) {
        if (line.startsWith("READY ")) {
          try {
            const payload = JSON.parse(line.slice(6)) as { origin?: string; token?: string };
            if (typeof payload.origin === "string" && typeof payload.token === "string") {
              clearTimeout(timer);
              resolve({ origin: payload.origin, token: payload.token });
              return;
            }
          } catch {
            /* 继续等 */
          }
        }
      }
    };
    proc.stdout?.on("data", onData);
    proc.stderr?.on("data", onData);
    proc.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`paste server 提前退出（code ${code}）`));
    });
  });
  return { ...ready, proc };
}

async function stopProc(proc: ChildProcess): Promise<void> {
  if (proc.exitCode !== null) return;
  proc.stdin?.write("\n");
  await new Promise<void>((resolve) => {
    const timer = setTimeout(() => resolve(), 5_000);
    proc.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
  if (proc.exitCode === null && proc.pid !== undefined) {
    try {
      process.kill(-proc.pid, "SIGTERM");
    } catch {
      /* 进程组可能已退出 */
    }
  }
}

test("gate 粘贴换发：页内换发成功后可发任务（复审 F2 回归）", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  const own = await spawnServer();
  const context = await browserHandle!.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  try {
    await page.goto(`${own.origin}/`);
    await page
      .getByPlaceholder("粘贴一键链接或口令")
      .fill(`${own.origin}/#bootstrap=${own.token}`);
    await page.getByRole("button", { name: "进入" }).click();
    // 换发成功：门消失、工作台渲染；关键是 composer 回到默认占位符——
    // reload 语义会落成「只读态（无 CSRF 凭证）」占位符并禁用发送。
    await expect(page.locator(".gate-card")).toHaveCount(0, { timeout: 20_000 });
    await expect(page.locator(".brand strong")).toHaveText("NexusCLI");
    await page.locator("textarea").first().fill("检查样例 Python 仓库。");
    await expect(page.locator(".send-button")).toBeEnabled();
  } finally {
    await context.close();
    await stopProc(own.proc);
  }
});

// 2026-10-27_workbench-ux-iteration U1 回归：坏口令曾把 Gate 卡死在 bootstrapping
// 分支（redeem 失败不还原 auth phase），整页无错误信息、无重试入口。
// 独立 spawn 一台 server：坏口令尝试不烧真口令，但重试走同一台的一次性口令。
test("U1 回归：坏口令就地报错，表单保留可重试", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  const own = await spawnServer();
  const context = await browserHandle!.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  try {
    await page.goto(`${own.origin}/`);
    const input = page.getByPlaceholder("粘贴一键链接或口令");
    await input.fill("wrong-token-abc");
    await page.getByRole("button", { name: "进入" }).click();
    // 错误就地可见（不再卡「正在完成一次性认证…」），且输入框仍在可重试
    await expect(page.locator(".gate-error")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator(".gate-error")).toContainText("口令无效或已被使用");
    await expect(input).toBeVisible();
    // 用同一表单填正确口令 → 进入工作台
    await input.fill(`${own.origin}/#bootstrap=${own.token}`);
    await page.getByRole("button", { name: "进入" }).click();
    await expect(page.locator(".gate-card")).toHaveCount(0, { timeout: 20_000 });
    await expect(page.locator(".brand strong")).toHaveText("NexusCLI");
  } finally {
    await context.close();
    await stopProc(own.proc);
  }
});

// 2026-10-27_workbench-ux-iteration U2 回归：csrf 仅内存，刷新后曾落成「只读死局」
// （composer 禁用，一键链接已烧）。修复后 initialize 探测 cookie 有效即自动重签。
test("U2 回归：刷新后 csrf 自动重签恢复可写，且重签 csrf 能真发变更请求", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  test.skip(authedPage === null, "前置用例未产出已认证页面（bootstrap 认证流失败）。");
  const page = authedPage as Page;
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.locator(".brand strong")).toHaveText("NexusCLI", { timeout: 20_000 });
  // 只读死局回归断言：修复前 send-button 恒 disabled 且占位符变「只读态」提示
  await page.locator("textarea").first().fill("刷新后可写性检查");
  await expect(page.locator(".send-button")).toBeEnabled({ timeout: 10_000 });
  // 端到端一跳补齐（2026-10-27 复审）：可写性不止于前端 canPost——用重签恢复的
  // csrf 经 UI 真发一次变更请求（startTurn 先 POST /v1/threads 建线程），服务端 200
  // 才算端到端恢复（服务端半边由 test_reissue_with_cookie_returns_current_csrf 的
  // 真实 POST /v1/threads 在单测层覆盖）。
  const createThreadStatuses: number[] = [];
  page.on("response", (response) => {
    if (
      new URL(response.url()).pathname === "/v1/threads" &&
      response.request().method() === "POST"
    ) {
      createThreadStatuses.push(response.status());
    }
  });
  await page.locator(".send-button").click();
  // 线程创建成功 → selectThread 把 hash 切到新线程；403 会停留在原视图并出
  // 「创建线程失败：csrf token missing or invalid」notice。
  await expect(page).toHaveURL(/#\/threads\/thread_/, { timeout: 15_000 });
  expect(createThreadStatuses).toEqual([200]);
  // 重签 csrf 被服务端拒收的任何形态（建线程 403 或后续 turn POST 403）都不允许出现
  await expect(page.getByText(/csrf token missing or invalid/)).toHaveCount(0);
});

test("发起 turn：幂等 POST + 审批门 + 事件 seq 严格连续", async ({ request }) => {
  test.skip(!harnessReady, skipReason);
  // x-api-key 头通道（外部自动化通道，csrf 豁免；浏览器 cookie 通道的 UI 提交
  // 另经 Composer/useWorkbench，不在本用例面）。API 基线不消费 authedContext：
  // 本地无 dist 时 bootstrap 用例按 uiSkipReason skip、authedContext 保持 null，
  // 曾取 authedContext!.request 抛 TypeError 并因 serial 中断后续用例。改用
  // request fixture（= playwright.request.newContext 的无 cookie 独立
  // APIRequestContext；仅驱动连接、不 launch 浏览器，不触碰文件头规避内建
  // browser/page fixture 的守则），全部请求凭 x-api-key 头认证（api.py 的
  // api_key 通道，csrf 豁免）——beforeAll 探测自述的「API 基线（发起 turn）
  // 照常执行」由此在本地无 dist 时成立。
  const authHeaders = { "x-api-key": apiKey };
  const apiRequest = request;
  const created = await apiRequest.post(`${origin}/v1/threads`, {
    headers: { ...authHeaders, "content-type": "application/json" },
    data: {},
  });
  if (created.status() === 401) {
    test.skip(
      true,
      "validation server 未接受本用例的 api key（脚本自管 key），无法经头通道发起 turn。",
    );
    return;
  }
  expect(created.ok()).toBe(true);
  const { id: threadId } = (await created.json()) as { id: string };
  expect(threadId).toMatch(/^thread_/);

  // turn POST 是同步阻塞（直到整个 turn 终态）；离线 demo 链的 write_file 会走
  // 真实审批门（F6 同步阻塞桥：登记后阻塞等 Web 决策，approvals.py:230-256）——
  // 在 POST 在途期间轮询审批卡并 approve，让 turn 真实走完（read → 审批写报告 →
  // 读回 → 汇总）。
  const requestId = crypto.randomUUID();
  const body = { message: "e2e 基线任务：检查样例数据并生成检查报告。", mode: "react" };
  const turnPromise = apiRequest.post(`${origin}/v1/threads/${threadId}/turns`, {
    headers: { ...authHeaders, "content-type": "application/json", "x-request-id": requestId },
    data: body,
  });
  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
  let turn: Awaited<typeof turnPromise> | null = null;
  for (let round = 0; round < 120 && turn === null; round++) {
    const settled = await Promise.race([
      turnPromise.then((response) => ({ done: true as const, response })),
      sleep(250).then(() => ({ done: false as const, response: null })),
    ]);
    if (settled.done) {
      turn = settled.response;
      break;
    }
    const listing = await apiRequest.get(`${origin}/v1/approvals`, { headers: authHeaders });
    if (!listing.ok()) continue;
    const payload = (await listing.json()) as {
      approvals?: { id: string; scope_type: string; scope_id: string }[];
    };
    const card = (payload.approvals ?? []).find(
      (item) => item.scope_type === "thread" && item.scope_id === threadId,
    );
    if (card !== undefined) {
      const decision = await apiRequest.post(
        `${origin}/v1/approvals/${encodeURIComponent(card.id)}/decision`,
        { headers: authHeaders, data: { decision: "approve" } },
      );
      expect(decision.ok()).toBe(true);
    }
  }
  if (turn === null) {
    throw new Error("turn 120 个轮询周期（约 30s）内未达终态");
  }
  if (turn.status() === 400) {
    const payload = (await turn.json().catch(() => ({}))) as { error?: string };
    if (payload.error?.includes("NEXUSCLI_API_KEY is not configured")) {
      test.skip(
        true,
        "离线 Demo provider 未就绪（A11/lane2）：服务无 LLM key 且离线注入缝未启用，无法真实发起 turn。",
      );
      return;
    }
  }
  expect(turn.ok()).toBe(true);
  const turnResult = (await turn.json()) as { total_tokens: number; text: string };
  // 离线 demo 的终稿文案（offline_demo.py _decide 收尾分支）证明链路真实走完
  expect(turnResult.text).toContain("检查报告");

  // A2 幂等门基线：同 x-request-id 同 payload 重放 → 同一回执，不二次执行
  // （真服务探针已验证该语义；此处对 turn 端点复验）。
  const replay = await apiRequest.post(`${origin}/v1/threads/${threadId}/turns`, {
    headers: { ...authHeaders, "content-type": "application/json", "x-request-id": requestId },
    data: body,
  });
  expect(replay.status()).toBe(turn.status());
  expect(await replay.json()).toEqual(turnResult);

  // A7 seq 基线：事件历史 per-thread seq 自 1 严格 +1；state.replayEvents 折叠整段
  // 历史不抛 SequenceGap（= 快照重建路径对真服务历史的校验）。
  const events = await apiRequest.get(`${origin}/v1/threads/${threadId}/events`, {
    headers: authHeaders,
  });
  expect(events.ok()).toBe(true);
  const text = await events.text();
  const seqs: number[] = [];
  for (const line of text.split("\n")) {
    if (!line.startsWith("data: ")) continue;
    const data = JSON.parse(line.slice(6)) as { seq?: unknown };
    if (typeof data.seq === "number") seqs.push(data.seq);
  }
  expect(seqs.length).toBeGreaterThan(0);
  expect(seqs).toEqual(Array.from({ length: seqs.length }, (_, index) => index + 1));
  const frames = text
    .split("\n\n")
    .filter((block) => block !== "")
    .map((block) => {
      const eventLine = block.split("\n").find((line) => line.startsWith("event: "));
      const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
      return {
        id: 0,
        event: eventLine?.slice(7) ?? "",
        data: JSON.parse((dataLine ?? "data: {}").slice(6)) as Record<string, unknown>,
      };
    });
  const state = replayEvents(threadId, frames);
  expect(state.seq).toBe(seqs.length);
  // 真实链路的落地证据：turn.started、tool_call（demo 链真实工具调用）与
  // turn.completed 都必须落库
  const eventNames = frames.map((frame) => frame.event);
  expect(eventNames).toContain("turn.started");
  expect(eventNames).toContain("tool_call");
  expect(eventNames).toContain("turn.completed");
});

test("会话列表：默认 Tab 渲染服务端会话面", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  test.skip(authedPage === null, "前置用例未产出已认证页面（bootstrap 认证流失败）。");
  const page = authedPage as Page;
  // 侧栏两组会话列表（工作会话 = 本浏览器线程记忆；会话回放 = 服务端枚举）
  await expect(page.locator(".section-caption", { hasText: "工作会话" })).toBeVisible();
  await expect(page.locator(".section-caption", { hasText: "会话回放" })).toBeVisible();
  // 服务端枚举面：空列表提示或会话条目二选一（取决于服务端会话库存量）。
  // .first()：两组列表并存——工作会话有条目时会话回放仍可显示空态提示，
  // .or() 会解析出两个可见元素，严格模式要求取其一（U2 回归用例真实发送后必现）。
  await expect(
    page
      .getByText("当前项目没有已保存会话")
      .or(page.locator(".session-item"))
      .first(),
  ).toBeVisible();
});

test("任务/用量面板：副标题为用户文案（U7 同步）", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  test.skip(authedPage === null, "前置用例未产出已认证页面（bootstrap 认证流失败）。");
  const page = authedPage as Page;
  await page.locator(".view-switch").getByRole("button", { name: "任务" }).click();
  // U7：副标题改用户视角文案（旧文案「每 2s 轮询 GET /v1/tasks」是实现细节，不进界面）
  await expect(page.getByText("后台队列任务实时刷新，可在此取消", { exact: true })).toBeVisible();
  // 任务面：空列表提示或任务表格二选一
  await expect(page.getByText("没有后台任务").or(page.locator("table.data-table"))).toBeVisible();
  await page.locator(".view-switch").getByRole("button", { name: "用量" }).click();
  // U7：用量副标题同样用户化（旧文案「GET /v1/usage/summary（= UsageStore.stats 原样）」）
  await expect(page.getByText("近 7 天回合、Token 与成本汇总", { exact: false })).toBeVisible();
});

// ---------------------------------------------------------------------------
// 2026-10-27_workbench-ux-round2：W1 慢等待提示 / U6 报告证据列表 / W2 会话回放可辨识
// ---------------------------------------------------------------------------

/** 与 Sidebar.formatSessionStamp 同口径的期望格式（本地时区；浏览器继承系统时区）。 */
function expectedSessionStamp(unixSeconds: number): string {
  const date = new Date(unixSeconds * 1000);
  const pad = (value: number): string => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    ` ${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** 向服务端 sessions 目录植入一条 title 为空的 REPL 会话（首行 meta，形状对齐
 *  store.py:696 与 reads.list_sessions 的 type:"meta" + cwd realpath 过滤）。 */
function seedUntitledSession(root: string): { id: string; updatedAt: number } {
  const dir = path.join(root, ".nexuscli", "sessions");
  mkdirSync(dir, { recursive: true });
  const updatedAt = Math.floor(Date.now() / 1000);
  const id = `e2e-untitled-${updatedAt}`;
  const meta = {
    type: "meta",
    id,
    cwd: path.join(root, "demo"),
    created_at: updatedAt,
    updated_at: updatedAt,
    model: "demo-model",
    provider: "offline-demo",
    title: "",
    message_count: 0,
    forked_from: "",
  };
  writeFileSync(path.join(dir, `${id}.jsonl`), `${JSON.stringify(meta)}\n`, "utf-8");
  return { id, updatedAt };
}

test("W1：执行中事件停滞超过阈值出现慢等待提示，不改变 busy 语义", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  test.skip(authedPage === null, "前置用例未产出已认证页面（bootstrap 认证流失败）。");
  const page = authedPage as Page;
  // 前置清理：U2 用例的 UI turn 仍阻塞在 write_file 审批（nexuscli:pending 记录会让
  // reload 后落成「有未确认的原请求」禁发态）。经 x-api-key 通道批准全部挂起审批，
  // 让该 turn 真实走完；随后页面出「turn 完成」回执且 pending 记录随之清除。
  const authHeaders = { "x-api-key": apiKey };
  const apiRequest = authedContext!.request;
  const listing = await apiRequest.get(`${origin}/v1/approvals`, { headers: authHeaders });
  if (listing.ok()) {
    const payload = (await listing.json()) as { approvals?: { id: string }[] };
    for (const item of payload.approvals ?? []) {
      const decision = await apiRequest.post(
        `${origin}/v1/approvals/${encodeURIComponent(item.id)}/decision`,
        { headers: authHeaders, data: { decision: "approve" } },
      );
      expect(decision.ok()).toBe(true);
    }
  }
  await expect(page.locator(".notice-banner")).toContainText(/turn 完成|原请求已完成/, {
    timeout: 30_000,
  });
  // W1 注入点（见 ChatPanel.tsx 顶部注释）：阈值键 nexuscli:slow-hint-ms，写 200ms 后
  // reload 生效（组件挂载时读取）。demo 链 write_file 审批前必然有 ≥200ms 事件空窗。
  await page.evaluate(() => window.localStorage.setItem("nexuscli:slow-hint-ms", "200"));
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.locator(".brand strong")).toHaveText("NexusCLI", { timeout: 20_000 });
  // 新建会话发一个真实 UI turn（避开 U2 的在途线程）
  await page.getByRole("button", { name: "新建会话" }).click();
  await page.locator("textarea").first().fill("W1 链路验证：检查样例数据并生成检查报告。");
  await expect(page.locator(".send-button")).toBeEnabled({ timeout: 10_000 });
  await page.locator(".send-button").click();
  // 提示在 3s 内出现（断言口径：200ms 阈值 + 审批前空窗，远小于 3s）
  const hint = page.getByTestId("slow-wait-hint");
  await expect(hint).toBeVisible({ timeout: 3_000 });
  await expect(hint).toContainText("仍在等待模型响应…（网络慢或模型负载高时可另开会话）");
  // 不自动取消：提示出现时 turn 仍在途（run-inline 未消失）
  await expect(page.locator(".run-inline").first()).toBeVisible();
  // 让 turn 真实走完（U6 用例依赖这条已完成线程的工具清单）：批准本线程的审批卡
  const card = page.locator(".approval-card", { hasText: "本线程" }).first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  await card.getByRole("button", { name: "批准" }).click();
  await expect(page.locator(".view-report")).toBeVisible({ timeout: 30_000 });
  // turn 终态后 busy 复位 → 提示随之消失（它只描述在途等待）
  await expect(hint).toHaveCount(0);
  // 还原阈值，避免影响后续用例的页面状态
  await page.evaluate(() => window.localStorage.removeItem("nexuscli:slow-hint-ms"));
});

test("U6：结果 tab 的「执行事实与来源」逐工具列出，点击跳执行详情", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  test.skip(authedPage === null, "前置用例未产出已认证页面（bootstrap 认证流失败）。");
  const page = authedPage as Page;
  await page.getByRole("tab", { name: "结果" }).click();
  const rows = page.getByTestId("evidence-tool-row");
  // demo 链至少有 read_file；每行 = 工具名 + 状态字
  const readRow = rows.filter({ hasText: "read_file" }).first();
  await expect(readRow).toBeVisible({ timeout: 10_000 });
  expect(await rows.count()).toBeGreaterThanOrEqual(1);
  await expect(readRow).toContainText("已完成");
  // 旧的单行聚合文案不再出现（替换，而非并存）
  await expect(page.locator(".evidence-list").getByText(/次工具调用/)).toHaveCount(0);
  // 点击证据行 → 跳执行 tab 并选中该工具（复用 choose 通路）
  await readRow.click();
  await expect(page.getByRole("tab", { name: /执行过程/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".tool-detail .detail-heading strong")).toHaveText("read_file");
});

// ---------------------------------------------------------------------------
// 0021：审批卡片扩展动词（本轮放行 = approve_run / 切换自动模式 = approve_auto）
// 与 Reason 行。置于 W1/U6 之后：W1 的前置清理（经 API 批准 U2 在途 turn 的审批）
// 完成后页面 pending 已清、composer 可发。点击路径走 run 动词（切换自动模式会改
// 会话权限、影响其他用例的审批前提，其语义由服务端测试 test_approve_auto_* 锁定）；
// approve_run 的「本轮内后续同工具调用不再询问」由服务端
// test_approve_run_covers_rest_of_run_via_executor 锁定，这里覆盖按钮可见性与
// 点击后的卡片收口 + turn 真实完成。
// ---------------------------------------------------------------------------

test("0021 审批卡片：run/auto 按钮与 Reason 行可见，本轮放行后卡片收口且 turn 完成", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  test.skip(authedPage === null, "前置用例未产出已认证页面（bootstrap 认证流失败）。");
  const page = authedPage as Page;
  await page.getByRole("button", { name: "新建会话" }).click();
  await page
    .locator("textarea")
    .first()
    .fill("0021 审批按钮链路验证：检查样例数据并生成检查报告。");
  await expect(page.locator(".send-button")).toBeEnabled({ timeout: 10_000 });
  await page.locator(".send-button").click();
  const card = page.locator(".approval-card", { hasText: "本线程" }).first();
  await expect(card).toBeVisible({ timeout: 15_000 });
  // Reason 行（服务端契约：description 必现）与四个决策动词按钮
  await expect(card.locator(".reason")).toBeVisible();
  await expect(card.getByRole("button", { name: "批准" })).toBeVisible();
  await expect(card.getByRole("button", { name: "拒绝" })).toBeVisible();
  const runButton = card.getByRole("button", { name: "本轮放行" });
  const autoButton = card.getByRole("button", { name: "切换自动模式" });
  await expect(runButton).toBeVisible();
  await expect(autoButton).toBeVisible();
  // 点击本轮放行：卡片收口消失（决策端点 200 → 轮询/SSE 刷新移除），
  // turn 真实走完（运行报告就绪 = demo 链尾端到端完成）。
  await runButton.click();
  await expect(card).toHaveCount(0, { timeout: 10_000 });
  await expect(page.locator(".view-report")).toBeVisible({ timeout: 30_000 });
  // 人工决定计数由 SSE approval_resolved 事件驱动（state.ts 无 seq 审批帧旁路 →
  // derive.ts approvals 计数）。2026-11-08 前该帧被 applyEvent 丢弃、计数恒 0——
  // 此断言锁死该回归。断言非零而非精确 1：demo 链若触发多张不同工具的审批卡，
  // 计数随真实决策次数增长。
  await page.locator(".view-report").click();
  const approvalsStat = page
    .locator(".report-stats > div")
    .filter({ hasText: "人工决定" })
    .locator("strong");
  await expect(approvalsStat).toHaveText(/[1-9]\d*/, { timeout: 10_000 });
});

test("W2：无标题 REPL 会话回退显示「会话 YYYY-MM-DD HH:MM」，不再出现 (untitled)", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  test.skip(authedPage === null, "前置用例未产出已认证页面（bootstrap 认证流失败）。");
  test.skip(
    serverRoot === null,
    "未能定位 validation server 的临时根目录（TMPDIR 探测失败），无法植入无标题会话。",
  );
  const page = authedPage as Page;
  const seeded = seedUntitledSession(serverRoot as string);
  // 侧栏在挂载与路由切换时重新拉会话列表（Sidebar useEffect [route]）——reload 后必达
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.locator(".brand strong")).toHaveText("NexusCLI", { timeout: 20_000 });
  const stamp = expectedSessionStamp(seeded.updatedAt);
  const entry = page.locator(".session-item").filter({ hasText: `会话 ${stamp}` }).first();
  await expect(entry).toBeVisible({ timeout: 10_000 });
  await expect(entry.locator("strong")).toHaveText(`会话 ${stamp}`);
  await expect(entry).toContainText("0 条消息");
  // 裸 "(untitled)" 文案在整个侧栏不允许出现
  await expect(page.locator(".sidebar").getByText("(untitled)")).toHaveCount(0);
});
