import { useCallback, useEffect, useState, type JSX } from "react";
import { ChevronRight, Menu, Radio, SquareTerminal } from "lucide-react";

import { ApiError } from "./api/client";
import { listApprovals, type PendingApproval } from "./api/approvals";
import { useWorkbench } from "./useWorkbench";
import { ChatPanel } from "./views/ChatPanel";
import { Inspector } from "./views/Inspector";
import { ReplayView } from "./views/ReplayView";
import { Sidebar, type SidebarRoute } from "./views/Sidebar";
import { TasksView } from "./views/TasksView";
import { UsageView } from "./views/UsageView";

// 工作台根组件（2026-10-26_workbench-kitagent-ui task_0003/0004 重写）。布局
// Ported from kitagent (MIT, zed123214/kitagent) frontend/src/App.tsx：
// sidebar + topbar + (chat | data-panel) + inspector + statusbar。数据编排
// 换用本仓 A12 的 useWorkbench（bootstrap/pending 幂等恢复/SSE 重连）；
// 审批保留 2s 轮询（A10 的 SSE 审批事件到达时立即补一轮）。

type Route = SidebarRoute;

// 畸形百分号编码（如 #/threads/%zz）不得炸掉整页挂载（复审 F5）：解码失败回退原文。
function decodeHashId(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function readRoute(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  if (hash.startsWith("/threads/")) {
    return { kind: "thread", id: decodeHashId(hash.slice("/threads/".length)) };
  }
  if (hash.startsWith("/sessions/")) {
    return { kind: "replay", id: decodeHashId(hash.slice("/sessions/".length)) };
  }
  if (hash === "/tasks" || hash === "/usage") {
    return { kind: hash.slice(1) as "tasks" | "usage" };
  }
  return { kind: "empty" };
}

function navigateTo(route: Route): void {
  switch (route.kind) {
    case "thread":
      window.location.hash = `/threads/${encodeURIComponent(route.id)}`;
      break;
    case "replay":
      window.location.hash = `/sessions/${encodeURIComponent(route.id)}`;
      break;
    case "tasks":
    case "usage":
      window.location.hash = `/${route.kind}`;
      break;
    default:
      window.location.hash = "";
  }
}

const CONNECTION_TEXT: Record<string, string> = {
  connecting: "连接中",
  live: "实时连接",
  reconnecting: "连接恢复中",
  error: "连接断开",
};

export function App(): JSX.Element {
  const wb = useWorkbench();
  const [route, setRoute] = useState<Route>(() => readRoute());
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  useEffect(() => {
    const onHash = (): void => setRoute(readRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // 常驻审批轮询（2s，01 定稿节奏）；SSE approval 事件由 state.ts 折叠，
  // 这里补一层「事件驱动立即轮询」避免审批卡迟到。
  const refreshApprovals = useCallback((): void => {
    listApprovals()
      .then((payload) => setApprovals(payload.approvals))
      .catch(() => {
        // 轮询失败静默重试
      });
  }, []);
  useEffect(() => {
    if (wb.auth.phase !== "ready") {
      return;
    }
    refreshApprovals();
    const timer = setInterval(refreshApprovals, 2000);
    return () => {
      clearInterval(timer);
    };
  }, [wb.auth.phase, refreshApprovals]);

  const onDecision = useCallback(
    (id: string): void => {
      setApprovals((list) => list.filter((item) => item.id !== id));
      refreshApprovals();
    },
    [refreshApprovals],
  );

  const navigate = useCallback(
    (next: Route): void => {
      if (next.kind === "thread") {
        wb.selectThread(next.id);
      } else {
        navigateTo(next);
      }
      setRoute(next.kind === "thread" ? next : readRoute());
      setSidebarOpen(false);
    },
    [wb],
  );

  if (wb.auth.phase !== "ready") {
    return (
      <Gate
        phase={wb.auth.phase === "error" ? "waiting" : wb.auth.phase}
        message={wb.auth.phase === "error" ? wb.auth.message : null}
        onRedeem={wb.redeem}
      />
    );
  }

  const canPost = wb.auth.phase === "ready" && wb.auth.canPost && wb.pending === null;
  const disabledHint =
    wb.auth.canPost === false
      ? "当前页面为只读态（无 CSRF 凭证），请用一键链接重新进入"
      : wb.pending !== null
        ? "有未确认的原请求，确认或解除后再发起新任务"
        : null;

  const lastUserMessage =
    [...(wb.workbench?.events ?? [])].reverse().find((event) => event.kind === "turn.started") ?? null;
  const breadcrumbTitle =
    route.kind === "tasks"
      ? "后台任务"
      : route.kind === "usage"
        ? "用量统计"
        : route.kind === "replay"
          ? `回放 ${route.id.slice(-12)}`
          : (lastUserMessage !== null && lastUserMessage.kind === "turn.started"
              ? lastUserMessage.message.slice(0, 40)
              : null) ?? "开始新任务";

  const online = route.kind === "thread" ? wb.streamState === "live" : true;
  const connectionText = route.kind === "thread" ? (CONNECTION_TEXT[wb.streamState] ?? "连接中") : "已连接";
  const currentId =
    route.kind === "replay" ? route.id : (wb.threadId ?? (route.kind === "thread" ? route.id : ""));

  return (
    <div
      className={`workbench ${sidebarOpen ? "sidebar-open" : ""} ${inspectorOpen ? "inspector-open" : ""}`}
    >
      <Sidebar route={route} onNavigate={navigate} onClose={() => setSidebarOpen(false)} />
      <main className="main">
        <header className="topbar">
          <button
            className="sidebar-toggle icon-button"
            aria-label="打开会话列表"
            onClick={() => setSidebarOpen(!sidebarOpen)}
          >
            <Menu size={19} />
          </button>
          <div className="breadcrumb">
            <span>工作台</span>
            <ChevronRight size={14} />
            <strong>{breadcrumbTitle}</strong>
          </div>
          <div className="topbar-right">
            <span className={`connection-pill ${online ? "online" : ""}`}>
              <span className="status-dot" />
              {connectionText}
            </span>
            {route.kind === "thread" && (
              <button
                className="inspector-toggle icon-button"
                aria-label="切换执行面板"
                onClick={() => setInspectorOpen(!inspectorOpen)}
              >
                <Radio size={18} />
              </button>
            )}
          </div>
        </header>
        {wb.notice !== null && (
          <div className="notice-banner" role="status">
            <span>{wb.notice}</span>
            {wb.pending !== null && (
              <>
                <button onClick={() => void wb.checkPending()}>查询原请求</button>
                <button disabled={wb.busy} onClick={wb.leavePending}>
                  解除等待（不重发）
                </button>
              </>
            )}
            <button aria-label="关闭提示" onClick={wb.dismissNotice}>
              ×
            </button>
          </div>
        )}
        {route.kind === "thread" && wb.streamError !== null && (
          // U3（2026-10-27_workbench-ux-iteration）：线程不存在/连接错误必须有显著
          // 呈现位——此前只进状态栏小字（「连接断开」），页面安静地显示欢迎页。
          <div className="error-banner" role="alert">
            <span>{wb.streamError}</span>
            <button onClick={() => navigate({ kind: "empty" })}>返回新建会话</button>
          </div>
        )}
        <div className="workspace">
          {route.kind === "tasks" && <TasksView active />}
          {route.kind === "usage" && <UsageView active />}
          {route.kind === "replay" && <ReplayView sessionId={route.id} />}
          {(route.kind === "empty" || route.kind === "thread") && (
            <>
              <ChatPanel
                workbench={wb.workbench}
                busy={wb.busy}
                canPost={canPost}
                disabledHint={disabledHint}
                waitingApproval={approvals.length > 0}
                onSend={wb.startTurn}
              />
              {route.kind === "thread" && (
                <Inspector
                  workbench={wb.workbench}
                  approvals={approvals}
                  connected={wb.streamState === "live"}
                  onDecision={onDecision}
                />
              )}
            </>
          )}
        </div>
        <footer className="statusbar">
          <span>
            <span className="status-dot" />
            {connectionText}
          </span>
          <span title={currentId}>{currentId !== "" ? currentId : "未选择会话"}</span>
          <span aria-live="polite">
            {wb.workbench !== null
              ? `${wb.workbench.events.length} 事件 · seq ${wb.workbench.seq}`
              : wb.busy
                ? "提交中"
                : "准备开始"}
          </span>
          <span>NexusCLI Runtime API</span>
        </footer>
      </main>
    </div>
  );
}

// 认证门（task_0006）：一次性口令语义不变（kitagent 对齐），新增两条自救路径——
// 粘贴一键链接/口令换发；以及服务端启动时落盘的 ~/.nexuscli/workbench-link.txt
// 指引（api.py 同轮新增）。换发成功后整页刷新，交给 useWorkbench 的 probe 走
// 标准就绪路径。
function Gate({
  phase,
  message,
  onRedeem,
}: {
  phase: "bootstrapping" | "waiting";
  message: string | null;
  onRedeem: (token: string) => Promise<void>;
}): JSX.Element {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(message);
  const [submitting, setSubmitting] = useState(false);

  // 粘贴换发经 useWorkbench.redeem 页内完成（复审 F2：reload 会丢 csrf 落成只读态
  // 且口令已烧）。失败信息就地呈现，不整页刷新。
  const submit = (): void => {
    const raw = input.trim();
    if (raw === "" || submitting) {
      return;
    }
    const token = raw.includes("#bootstrap=") ? raw.split("#bootstrap=")[1].trim() : raw;
    setSubmitting(true);
    setError(null);
    onRedeem(token).catch((cause: unknown) => {
      setSubmitting(false);
      setError(
        cause instanceof ApiError && cause.status === 403
          ? "口令无效或已被使用。每个服务实例的口令一次性；请取回当前实例的一键链接（见下方说明）。"
          : cause instanceof Error
            ? cause.message
            : String(cause),
      );
    });
  };

  return (
    <div className="gate-wrap">
      <div className="gate-card">
        <div className="welcome-mark">
          <SquareTerminal size={29} />
        </div>
        <h1>NexusCLI Workbench</h1>
        {phase === "bootstrapping" ? (
          <p>正在完成一次性认证…</p>
        ) : (
          <>
            <p>
              本机工作台使用一次性口令认证：打开服务启动输出的一键链接（形如{" "}
              <code>http://127.0.0.1:&lt;port&gt;/#bootstrap=…</code>），或把该链接 / 口令粘贴到下面。
            </p>
            <form
              className="gate-form"
              onSubmit={(event) => {
                event.preventDefault();
                submit();
              }}
            >
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="粘贴一键链接或口令"
                aria-label="一键链接或口令"
              />
              <button type="submit" className="primary" disabled={submitting || input.trim() === ""}>
                进入
              </button>
            </form>
            {error !== null && <p className="gate-error">{error}</p>}
            <div className="gate-foot">
              一键链接每次服务启动都会刷新，同时写入{" "}
              <code>~/.nexuscli/workbench-link.txt</code>（权限 0600）；也可在服务启动日志中找到。
              认证凭证由一次性口令换发的 HttpOnly cookie 承载，无需输入任何 API key。
            </div>
          </>
        )}
      </div>
    </div>
  );
}
