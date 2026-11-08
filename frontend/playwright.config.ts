// playwright.config.ts：workbench e2e 基线的运行形态（task_0020 把 Playwright e2e
// 接入 CI）。与 workbench.spec.ts 的分工：validation server（scripts/
// web_validation_server.py，真实 Core + 离线 demo provider）由 spec 的 beforeAll
// 拉起并握手 READY——一次性口令只能运行期获取，不能在配置里静态声明，故不用
// config 的 webServer 钩子；本文件只锁四件套：
//
//   - baseURL：真实 origin 运行期才确定（server --port 0 由系统分配自由端口，避免
//     固定端口在 CI 撞车），spec 拿到 READY 后以绝对 URL 导航；此处的 baseURL 是
//     相对 URL 导航与 request fixture 的兜底缺省（指回回环本机）。
//   - serial：workbench.spec.ts 顶部 test.describe.configure({ mode: "serial" })
//     已声明（前序用例失败则后续跳过——一次性口令只能由用例 1 产出已认证页面）；
//     配置层 fullyParallel:false + workers:1 同口径兜底。
//   - retries 0：CI 中失败即失败，不靠重试掩盖抖动。
//   - trace on-first-retry：本地把 retries 调大排查抖动时自动留痕；retries 0 下
//     不产生 trace，CI 失败上传的是 test-results 里的错误上下文等失败附件。
//
// 浏览器/服务缺失时的 skip vs 硬失败语义见 workbench.spec.ts 顶部注释：本地可
// skip（e2e 是本地可选步骤），CI（env CI 置位）中必须失败，配置层不另设跳过逻辑。

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  // serial 语义的配置层兜底（文件内 describe.configure 为第一声明处）
  fullyParallel: false,
  workers: 1,
  // CI 上防 test.only 把整个基线静默缩水
  forbidOnly: !!process.env.CI,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1",
    trace: "on-first-retry",
  },
});
