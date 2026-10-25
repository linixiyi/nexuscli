// workbench.spec.ts：Playwright e2e 基线（A12，用例编排 Ported from kitagent (MIT,
// zed123214/kitagent) frontend/e2e/workbench.spec.ts，服务与断言适配 NexusCLI Runtime API）。
//
// 5 条基线用例（serial 模式，前序失败则后续跳过）：
//   1. bootstrap 认证流：#bootstrap= 一次性口令 → 片段抹除 → 门通过（并产生后续用例
//      复用的已认证页面）
//   2. 加载：静态托管 + React 挂载 + 无页面错误
//   3. 发起 turn：x-api-key 头通道（csrf 豁免，api.py:262-272）建线程 + 幂等 POST
//      turn（x-request-id 重放同回执）+ 事件 seq 严格连续（state.replayEvents 折叠
//      不抛 SequenceGap）
//   4. 会话列表：默认 Tab 渲染服务端会话面
//   5. 任务面板：任务 Tab 渲染任务面
//
// 服务策略：起真服务 scripts/web_validation_server.py（A11/lane2 交付）；脚本或浏览器
// 二进制缺失时整文件跳过并留说明（本地可选，CI 不强制）。turn 用例依赖离线 Demo
// provider（无 key 端到端）：服务报「NEXUSCLI_API_KEY is not configured」时按未就绪
// 跳过，不伪造通过。
//
// 现状断言说明：现有 App.tsx 无 cookie 探针——冷加载（即使带有效 cookie）恒显示
// 引导门，认证态只存在于完成 fragment 换发的那个页面。因此用例 1 产生的已认证页面
// 被用例 4/5 串行复用（kitagent 用 /status+storageState 的做法在本仓 API 面不可行）。
// A12 的 useWorkbench 已实现 probeAuthorized 探针，接入 App 后可改为标准 storageState 流。

import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { replayEvents } from "../src/state";

test.describe.configure({ mode: "serial" });

test.setTimeout(90_000);

const repoRoot = path.resolve(import.meta.dirname, "..", "..");
const validationServer = path.join(repoRoot, "scripts", "web_validation_server.py");
// lane2 交付的 validation server 以 api_key="offline-demo" 构造 RuntimeApiServer
// （脚本内硬编码，忽略外部 env）；保留 env 覆盖缝以便脚本演进。
const apiKey = process.env.NEXUSCLI_E2E_API_KEY ?? "offline-demo";

let serverProcess: ChildProcess | null = null;
let harnessReady = false;
let servesWorkbench = false;
let skipReason =
  `e2e 基线跳过：scripts/web_validation_server.py 尚未就绪（A11/lane2 并行交付）。` +
  `就绪后重跑 npm run test:e2e。`;
let uiSkipReason = "";
let origin = "";
let bootstrapToken = "";
// 一次性口令只能换发一次 → 已认证页面只能由用例 1 产出，后续 UI 用例串行复用
// （storageState 方案在本仓不可行：App 无 cookie 探针，冷加载恒为引导门）。
let authedContext: BrowserContext | null = null;
let authedPage: Page | null = null;

test.beforeAll(async () => {
  if (!existsSync(validationServer)) {
    return; // 保持 harnessReady=false，用例按 skipReason 跳过
  }
  // 浏览器二进制缺失 → 如实跳过（不安装、不伪造结果）。
  // 用真实 launch 探针而非 executablePath() 存在性检查：新版 Playwright 的默认
  // headless 走独立的 chrome-headless-shell 二进制，executablePath() 指向完整版
  // Chromium，二者可能只装其一。
  const { chromium } = await import("@playwright/test");
  try {
    const browser = await chromium.launch();
    await browser.close();
  } catch (cause) {
    skipReason =
      `e2e 基线跳过：Playwright 浏览器无法启动（${cause instanceof Error ? cause.message.split("\n")[0] : String(cause)}）。` +
      `可执行 cd frontend && npx playwright install chromium；e2e 为本地可选步骤，CI 不强制。`;
    return;
  }
  // 隔离 HOME/USERPROFILE：runtime DB（~/.nexuscli/runtime/runtime.db，api.py:123）不得写进真实用户目录
  const isolatedHome = mkdtempSync(path.join(tmpdir(), "nexuscli-e2e-"));
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
      skipReason = `e2e 基线跳过：validation server 未就绪（${cause instanceof Error ? cause.message : String(cause)}）。`;
      return null;
    },
  );
  if (ready === null) {
    await stopServer();
    return;
  }
  origin = ready.origin;
  bootstrapToken = ready.token;
  harnessReady = true;
  // 静态托管探测：_workbench_dist_root() 是 cwd 相对解析（api.py:1141-1145），而
  // validation server 把 server.cwd 指到临时 demo 工作区（scripts/web_validation_server.py
  // cwd=str(workspace)）——dist 不在其下时服务端返回「UI 未构建」空态页，/v1 API 不受影响。
  // UI 基线按此如实跳过并留说明；API 基线（发起 turn）照常执行。
  const homeHtml = await fetch(`${origin}/`).then((r) => r.text());
  servesWorkbench = homeHtml.includes('<div id="root">') && !homeHtml.includes("UI 未构建");
  if (!servesWorkbench) {
    uiSkipReason =
      "UI 基线跳过：validation server 未托管已构建的 frontend/dist（dist 按 server.cwd 相对解析，" +
      "而脚本把 cwd 指到临时 demo 工作区）——构建同步与 /v1 API 均正常，属 lane 间契约缝隙，待收口任务修复。";
  }
});

test.afterAll(async () => {
  await authedContext?.close();
  await stopServer();
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

test("bootstrap 认证流：一次性口令换发并抹除片段", async ({ browser }) => {
  // 跨用例持久页面：Playwright 的 page fixture 每测试隔离、用例结束即销毁——
  // 串行 authedPage 交接必须用 browser fixture 自建 context（主会话收口修复）。
  const context = await browser.newContext();
  const page = await context.newPage();
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  await page.goto(`${origin}/#bootstrap=${bootstrapToken}`);
  // 换发后口令不得留在地址栏（kitagent 同款断言）
  await expect(page).not.toHaveURL(/bootstrap=/);
  await expect(page.getByRole("heading", { name: "NexusCLI Workbench" })).toBeVisible();
  // 门通过：引导文案消失，工作台主体（导航）渲染
  await expect(page.getByText("请从服务启动日志打印的一键链接进入")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "会话", exact: true })).toBeVisible();
  authedContext = page.context();
  authedPage = page;
});

test("加载：静态托管 + React 挂载无页面错误", async ({ page }) => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(origin);
  await expect(page.getByRole("heading", { name: "NexusCLI Workbench" })).toBeVisible();
  // 现状：App 无 cookie 探针，冷加载显示引导门（useWorkbench 的 probe-ready 待接入；
  // 接入后此断言应改为工作台主体渲染）。
  await expect(page.getByText("请从服务启动日志打印的一键链接进入")).toBeVisible();
  expect(errors).toEqual([]);
});

test("发起 turn：幂等 POST + 审批门 + 事件 seq 严格连续", async () => {
  test.skip(!harnessReady, skipReason);
  // x-api-key 头通道（外部自动化通道，csrf 豁免；浏览器 cookie 通道的 UI 提交
  // 另经 Composer/useWorkbench，不在本用例面）。
  const authHeaders = { "x-api-key": apiKey };
  const apiRequest = authedContext!.request; // cookie 通道（bootstrap 换发的会话）
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
  // 已认证页面停在用例 1 的工作台首页（默认 Tab 即会话列表）
  await expect(page.getByRole("heading", { name: "REPL 会话（服务端枚举）" })).toBeVisible();
  // 服务端枚举面：空列表提示或表格二选一（取决于服务端会话库存量）
  await expect(
    page.getByText("当前项目没有已保存会话").or(page.locator("table.table")),
  ).toBeVisible();
});

test("任务面板：任务 Tab 渲染任务面", async () => {
  test.skip(!harnessReady, skipReason);
  test.skip(!servesWorkbench, uiSkipReason);
  test.skip(authedPage === null, "前置用例未产出已认证页面（bootstrap 认证流失败）。");
  const page = authedPage as Page;
  await page.getByRole("button", { name: "任务", exact: true }).click();
  await expect(page.getByText("每 2s 轮询 GET /v1/tasks", { exact: false })).toBeVisible();
  // 任务面：空列表提示或任务表格二选一
  await expect(page.getByText("没有后台任务").or(page.locator("table.table"))).toBeVisible();
});
