// useWorkbench.ts：工作台编排 hook（A12，Ported from kitagent (MIT, zed123214/kitagent)
// frontend/src/useWorkbench.ts；WS 语境改为本仓 SSE，键名 agentrt:pending → nexuscli:pending）。
//
// 编排三件事：
// 1. bootstrap：#bootstrap= 一次性口令换发 cookie+csrf（换发后抹掉片段；已认证的第二
//    标签页换发 403 时沿用既有 cookie，kitagent initialize:92-96 同款容错）。本仓差异：
//    无 GET /status，且 csrf 只在 bootstrap 响应里出现一次——刷新/第二标签页是
//    「cookie 只读」态（canPost=false）：GET 查询可用，POST 变更不可用。
// 2. pending 幂等恢复：发起 turn 前把未完成的 POST 记入 localStorage
//    nexuscli:pending（request_id 即 A2 幂等门的 x-request-id，api.py:277-297）；
//    刷新后恢复记录并**查询状态而非重复提交**——GET /v1/threads/{id}/events 证据
//    扫描（turn.started seq > 提交前 seen_seq 且 message 匹配 → 已送达；其后有
//    turn.completed/error → 已终态，清除记录不重发）。仅在用户显式「查询原请求」
//    且本页持有 csrf 时才经幂等门重放 POST：同 x-request-id 同 payload 会被服务端
//    重放/等待在途结果而不会二次执行（已用真服务探针验证：两次同 id 请求返回同一
//    线程 id）；跨进程重启后登记表为空，此时执行的是「中断恢复」而非重复执行
//    （completed 证据已短路，不会重放已完成的 turn）。
// 3. SSE 连接：手工 fetch 流（同 useEventStream 的契约约束）+ since 游标 + 退避重连
//    （kitagent connect/onclose:216 同款指数退避）；帧经 state.applyEvent 折叠，
//    SequenceGap 断档 → 重建状态并 since=0 全量回放（= kitagent publish 捕获异常
//    后关 socket 重连的同位，「重拉快照」）。
//
// 本 hook 自 2026-10-26_workbench-kitagent-ui 起接入 App.tsx（bootstrap/审批轮询/
// SSE 全部经此编排；gate 粘贴换发走 redeem——页内换发，绝不 reload）。

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, setCsrfToken } from "./api/client";
import type { SseFrame } from "./api/types";
import { rememberThread } from "./state/localThreads";
import {
  applyEvent,
  initialWorkbenchState,
  SequenceGap,
  type WorkbenchState,
} from "./state";

/** localStorage 持久化键（任务定稿键名）。 */
const PENDING_KEY = "nexuscli:pending";

/** 发起 turn 时未完成的 POST 记录（刷新后凭它查询，而不是重复提交）。 */
export type PendingTurn = {
  /** A2 幂等门的 x-request-id（crypto.randomUUID）。 */
  request_id: string;
  thread_id: string;
  message: string;
  mode: string;
  /** 提交前该线程已见的最大 per-thread seq（A7）：证据扫描只认比它新的 turn.started。 */
  seen_seq: number;
  /** ISO 时间戳，诊断用。 */
  started_at: string;
};

export type WorkbenchAuth =
  | { phase: "bootstrapping" }
  | { phase: "ready"; canPost: boolean }
  | { phase: "waiting" }
  | { phase: "error"; message: string };

export type StreamStatus = "connecting" | "live" | "reconnecting" | "error";

type TurnResult = { thread_id: string; text: string; mode: string; total_tokens: number };

type RecoveryVerdict =
  | { verdict: "completed"; text: string; tokens: number }
  | { verdict: "failed"; error: string }
  | { verdict: "inflight" }
  | { verdict: "not_started" }
  | { verdict: "thread_missing" }
  | { verdict: "unreachable"; reason: string };

function loadPendingRecord(): PendingTurn | null {
  try {
    const raw = localStorage.getItem(PENDING_KEY);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (
      typeof parsed !== "object" ||
      parsed === null ||
      typeof (parsed as Record<string, unknown>).request_id !== "string" ||
      typeof (parsed as Record<string, unknown>).thread_id !== "string"
    ) {
      return null;
    }
    return parsed as PendingTurn;
  } catch {
    return null;
  }
}

function savePendingRecord(record: PendingTurn): void {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify(record));
  } catch {
    // 存储不可用时降级为仅内存：本页仍幂等（request_id 不变），刷新后无法恢复。
  }
}

function clearPendingRecord(): void {
  try {
    localStorage.removeItem(PENDING_KEY);
  } catch {
    // 同上，尽力而为
  }
}

function payloadSeq(data: Record<string, unknown>): number {
  const value = data.seq;
  return typeof value === "number" && Number.isInteger(value) && value >= 1 ? value : 0;
}

/** SSE 文本块 → 帧列表（id:/event:/data: 三段，注释行忽略；data 非法 JSON 保留原文）。 */
function parseSseFrames(chunk: string): SseFrame[] {
  const frames: SseFrame[] = [];
  let current: { id?: number; event?: string; data?: string } = {};
  let sawLine = false;
  for (const line of chunk.split("\n")) {
    if (line === "") {
      if (sawLine && current.event !== undefined && current.data !== undefined) {
        let data: Record<string, unknown>;
        try {
          data = JSON.parse(current.data) as Record<string, unknown>;
        } catch {
          data = { raw: current.data };
        }
        frames.push({ id: current.id ?? 0, event: current.event, data });
      }
      current = {};
      sawLine = false;
      continue;
    }
    if (line.startsWith(":")) continue; // 心跳/注释行
    sawLine = true;
    if (line.startsWith("id: ")) {
      const value = Number.parseInt(line.slice(4), 10);
      current.id = Number.isNaN(value) ? current.id : value;
    } else if (line.startsWith("event: ")) {
      current.event = line.slice(7);
    } else if (line.startsWith("data: ")) {
      current.data = line.slice(6);
    }
  }
  return frames;
}

async function fetchEventFrames(threadId: string): Promise<SseFrame[]> {
  const response = await fetch(`/v1/threads/${encodeURIComponent(threadId)}/events`);
  if (response.status === 404) {
    throw new ApiError(404, "thread not found");
  }
  if (!response.ok) {
    throw new ApiError(response.status, `HTTP ${response.status}`);
  }
  return parseSseFrames(await response.text());
}

function readMaxSeq(frames: SseFrame[]): number {
  let max = 0;
  for (const frame of frames) {
    const seq = payloadSeq(frame.data);
    if (seq > max) max = seq;
  }
  return max;
}

/**
 * 证据扫描（纯查询）：pending 记录的 turn 是否已送达/已终态。
 * 匹配条件 = seq > seen_seq 且 message 全等——A7 per-thread seq 单调保证不会误配
 * 历史上同文本的 turn。
 */
function scanRecovery(frames: SseFrame[], record: PendingTurn): RecoveryVerdict {
  let startedSeq = 0;
  for (const frame of frames) {
    const seq = payloadSeq(frame.data);
    if (
      frame.event === "turn.started" &&
      seq > record.seen_seq &&
      seq > startedSeq &&
      frame.data.message === record.message
    ) {
      startedSeq = seq;
    }
  }
  if (startedSeq === 0) return { verdict: "not_started" };
  for (const frame of frames) {
    const seq = payloadSeq(frame.data);
    if (seq <= startedSeq) continue;
    if (frame.event === "turn.completed") {
      return {
        verdict: "completed",
        text: typeof frame.data.text === "string" ? frame.data.text : "",
        tokens: typeof frame.data.total_tokens === "number" ? frame.data.total_tokens : 0,
      };
    }
    if (frame.event === "error") {
      const message = frame.data.message ?? frame.data.error;
      return { verdict: "failed", error: typeof message === "string" ? message : "未知错误" };
    }
  }
  return { verdict: "inflight" };
}

/** 服务端鉴权探针：GET 只读，cookie 通道即可（无需 csrf）。 */
async function probeAuthorized(): Promise<boolean> {
  try {
    const response = await fetch("/v1/sessions?limit=1");
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * U2（2026-10-27_workbench-ux-iteration）csrf 重签：凭有效会话 cookie 取回当前 csrf
 * （无体 POST；服务端只认 cookie 会话通道，401 = cookie 已失效）。
 * csrf 只存内存（F15 有意），刷新后本页是「cookie 只读」态——重签是唯一的页内恢复通道。
 * 服务端为收敛语义（2026-10-27 复审修复：重签不轮换）——多标签页/多次刷新取回同一
 * 值，本页重签不会使其他已打开标签页的 csrf 失效。
 */
type ReissueOutcome = { kind: "ok"; csrf: string } | { kind: "failed"; status: number };

async function reissueCsrf(): Promise<ReissueOutcome> {
  try {
    const response = await fetch("/v1/workbench/reissue-csrf", { method: "POST" });
    if (!response.ok) {
      return { kind: "failed", status: response.status };
    }
    const payload = (await response.json()) as { csrf?: unknown };
    if (typeof payload.csrf === "string" && payload.csrf !== "") {
      return { kind: "ok", csrf: payload.csrf };
    }
    return { kind: "failed", status: response.status };
  } catch {
    return { kind: "failed", status: 0 };
  }
}

/** 一次性口令换发（自持请求：需要拿到 csrf 响应体；client.ts 的封装不透出）。 */
async function bootstrapExchange(token: string): Promise<string> {
  const response = await fetch("/v1/workbench/bootstrap", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, `bootstrap 失败（HTTP ${response.status}）`);
  }
  const payload = (await response.json()) as { csrf?: unknown };
  if (typeof payload.csrf !== "string" || payload.csrf === "") {
    throw new ApiError(response.status, "bootstrap 响应缺少 csrf");
  }
  return payload.csrf;
}

async function readErrorText(response: Response): Promise<string> {
  const payload: unknown = await response.json().catch(() => ({}));
  const error = (payload as Record<string, unknown>)?.error;
  return typeof error === "string" ? error : `HTTP ${response.status}`;
}

/** 发起 turn 的幂等 POST（首次提交与重放查询共用同一形状；csrf 显式传入）。 */
async function postTurnIdempotent(record: PendingTurn, csrf: string): Promise<TurnResult> {
  const response = await fetch(`/v1/threads/${encodeURIComponent(record.thread_id)}/turns`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-csrf-token": csrf,
      "x-request-id": record.request_id,
    },
    body: JSON.stringify({ message: record.message, mode: record.mode }),
  });
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorText(response));
  }
  return (await response.json()) as TurnResult;
}

export function useWorkbench(): {
  auth: WorkbenchAuth;
  threadId: string | null;
  selectThread: (id: string) => void;
  workbench: WorkbenchState | null;
  streamState: StreamStatus;
  streamError: string | null;
  pending: PendingTurn | null;
  notice: string | null;
  busy: boolean;
  /** 发起 turn（新建线程 + 幂等 POST）；返回是否已取得终态回执。 */
  startTurn: (message: string, mode: string) => Promise<boolean>;
  /** 认证门粘贴换发（页内完成，不 reload）；失败原样抛错由 Gate 呈现。 */
  redeem: (token: string) => Promise<void>;
  /** 用户显式「查询原请求」：先纯查询，未决且持有 csrf 时经 A2 幂等门重放。 */
  checkPending: () => Promise<void>;
  /** 解除本页等待（不重发、不取消——kitagent leavePending 同款语义）。 */
  leavePending: () => void;
  dismissNotice: () => void;
} {
  const [auth, setAuth] = useState<WorkbenchAuth>(() => {
    if (window.location.hash.match(/^#bootstrap=(.+)$/)) {
      return { phase: "bootstrapping" };
    }
    return { phase: "waiting" };
  });
  const [threadId, setThreadId] = useState<string | null>(() => {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash.startsWith("/threads/")) return null;
    const raw = hash.slice("/threads/".length);
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw; // 畸形编码回退原文（复审 F5），不让白屏盖过可诊断性
    }
  });
  const [workbench, setWorkbench] = useState<WorkbenchState | null>(null);
  const [streamState, setStreamState] = useState<StreamStatus>("connecting");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const csrfRef = useRef<string | null>(null);
  const pendingRef = useRef<PendingTurn | null>(null);

  const selectThread = useCallback((id: string) => {
    setThreadId(id);
    setWorkbench(null);
    window.location.hash = `/threads/${encodeURIComponent(id)}`;
  }, []);

  const clearPending = useCallback((record: PendingTurn): void => {
    // 只清自己名下的记录：被遗弃的查询不得吞掉更新的一条（kitagent isPending/clearPending 同款）。
    if (pendingRef.current?.request_id !== record.request_id) return;
    pendingRef.current = null;
    clearPendingRecord();
    setPending(null);
  }, []);

  const resolvePending = useCallback(
    async (record: PendingTurn, allowReplay: boolean): Promise<void> => {
      let frames: SseFrame[];
      try {
        frames = await fetchEventFrames(record.thread_id);
      } catch (cause) {
        if (pendingRef.current?.request_id !== record.request_id) return;
        if (cause instanceof ApiError && cause.status === 404) {
          setNotice(
            "结果未确认：服务端已找不到该线程（实例可能已重启）。可解除等待（不会重发），或用新的一键链接重新进入。",
          );
        } else {
          setNotice(
            `暂时无法查询原请求状态（${cause instanceof Error ? cause.message : String(cause)}），稍后可再查询。`,
          );
        }
        return;
      }
      const outcome = scanRecovery(frames, record);
      if (pendingRef.current?.request_id !== record.request_id) return;
      if (outcome.verdict === "completed") {
        clearPending(record);
        setNotice(
          `原请求已完成（未重发）${outcome.tokens > 0 ? `，共 ${outcome.tokens} tokens` : ""}。`,
        );
        selectThread(record.thread_id);
        return;
      }
      if (outcome.verdict === "failed") {
        clearPending(record);
        setNotice(`原请求已失败（未重发）：${outcome.error}`);
        return;
      }
      if (outcome.verdict === "thread_missing") {
        setNotice("结果未确认：服务端已找不到该线程。可解除等待（不会重发）。");
        return;
      }
      if (outcome.verdict === "unreachable") {
        setNotice(`暂时无法查询原请求状态（${outcome.reason}），稍后可再查询。`);
        return;
      }
      if (allowReplay && csrfRef.current !== null) {
        // 用户显式重放：同 x-request-id 同 payload 经 A2 幂等门，不会二次执行。
        try {
          const result = await postTurnIdempotent(record, csrfRef.current);
          if (pendingRef.current?.request_id !== record.request_id) return;
          clearPending(record);
          setNotice(`原请求回执已确认（未重发）：${result.total_tokens} tokens。`);
          selectThread(record.thread_id);
        } catch (cause) {
          if (pendingRef.current?.request_id !== record.request_id) return;
          if (cause instanceof ApiError && cause.status < 500) {
            clearPending(record);
            setNotice(`原请求被服务端拒绝（未重发）：${cause.message}`);
          } else {
            setNotice(
              `重放查询失败（${cause instanceof Error ? cause.message : String(cause)}）；记录保留，可稍后再查。`,
            );
          }
        }
        return;
      }
      if (pendingRef.current?.request_id === record.request_id) {
        setNotice(
          csrfRef.current === null
            ? "原请求仍在处理中或结果未确认。当前页面无 CSRF 凭证（刷新后 cookie 只读），可稍后用一键链接重新进入查询，或解除等待（不会重发）。"
            : "原请求仍在处理中或结果未确认。可稍后再查询，或解除等待（不会重发）。",
        );
      }
    },
    [clearPending, selectThread],
  );

  // ------------------------------------------------------------------
  // bootstrap 编排（一次性：挂载时执行）
  // ------------------------------------------------------------------
  useEffect(() => {
    let live = true;
    const fragment = window.location.hash.match(/^#bootstrap=(.+)$/);
    const initialize = async (): Promise<void> => {
      if (fragment !== null) {
        const token = decodeURIComponent(fragment[1]);
        // 渲染任何用户内容前抹掉启动口令（kitagent initialize:87-97 同款）。
        window.history.replaceState(
          null,
          "",
          window.location.pathname + window.location.search,
        );
        try {
          const csrf = await bootstrapExchange(token);
          csrfRef.current = csrf;
          setCsrfToken(csrf); // 与 client.ts 的视图请求共享同一 csrf
        } catch {
          /* 已认证的第二标签页沿用既有 cookie（kitagent 同款容错） */
        }
      }
      const authorized = await probeAuthorized();
      if (!live) return;
      if (authorized) {
        // U2：cookie 有效但 csrf 仅内存（刷新/第二标签页冷加载）→ 自动重签恢复可写。
        // 服务端重签是收敛语义（返回当前值不轮换，2026-10-27 复审修复）：第二标签页
        // 冷加载重签不会使已打开旧标签页的 csrf 失效，多标签页各自恢复互不破坏。
        // 401（cookie 已失效，如服务重启清会话）→ 回 waiting 走 gate 自救（规格 §7
        // 风险缓解：不掩盖真只读场景）；其他失败（端点缺失/网络抖动）→ 维持原
        // 只读就绪态（canPost=false），不阻断浏览。
        if (csrfRef.current === null) {
          const reissue = await reissueCsrf();
          if (!live) return;
          if (reissue.kind === "ok") {
            csrfRef.current = reissue.csrf;
            setCsrfToken(reissue.csrf);
          } else if (reissue.status === 401) {
            setAuth({ phase: "waiting" });
            return;
          }
        }
        setAuth({ phase: "ready", canPost: csrfRef.current !== null });
        const record = loadPendingRecord();
        if (record !== null) {
          pendingRef.current = record;
          setPending(record);
          // 自动恢复只做纯查询（不重发）；未决时给出可操作的提示。
          void resolvePending(record, false);
        }
      } else {
        setAuth({ phase: "waiting" });
      }
    };
    void initialize();
    return () => {
      live = false;
    };
  }, [resolvePending]);

  const checkPending = useCallback(async (): Promise<void> => {
    const record = pendingRef.current;
    if (record === null) return;
    await resolvePending(record, true);
  }, [resolvePending]);

  /**
   * 认证门粘贴换发（2026-10-26 复审 F2 修复）：页内完成口令换发并直接进入就绪态，
   * **绝不 reload**——csrf 只存模块内存（client.ts:21-25），整页刷新即失，换发成功
   * 也会落成只读会话且一次性口令已被烧掉。bootstrapExchange 失败时原样抛错，
   * 由调用方（App 的 Gate）呈现。
   */
  const redeem = useCallback(
    async (token: string): Promise<void> => {
      setAuth({ phase: "bootstrapping" });
      try {
        const csrf = await bootstrapExchange(token);
        csrfRef.current = csrf;
        setCsrfToken(csrf);
        const authorized = await probeAuthorized();
        if (authorized) {
          setAuth({ phase: "ready", canPost: true });
          const record = loadPendingRecord();
          if (record !== null) {
            pendingRef.current = record;
            setPending(record);
            void resolvePending(record, false);
          }
        } else {
          setAuth({ phase: "waiting" });
        }
      } catch (cause) {
        // U1（2026-10-27_workbench-ux-iteration）：换发失败必须还原 waiting 态，
        // 否则 Gate 停在 bootstrapping 分支——错误信息与重试表单都被吞掉
        // （坏口令卡死「正在完成一次性认证…」）。错误原样上抛由 Gate 就地呈现。
        setAuth({ phase: "waiting" });
        throw cause;
      }
    },
    [resolvePending],
  );

  const leavePending = useCallback((): void => {
    const record = pendingRef.current;
    if (record === null || busy) return;
    if (
      !window.confirm(
        "仅解除本页等待，不会取消或重发原请求；原请求仍可能已被服务端接收。请核对线程与执行状态后，再决定是否提交新任务。继续？",
      )
    ) {
      return;
    }
    clearPending(record);
    setNotice("已解除本页等待。原请求未被取消，也未重发。");
  }, [busy, clearPending]);

  const startTurn = useCallback(
    async (message: string, mode: string): Promise<boolean> => {
      const trimmed = message.trim();
      if (busy || pendingRef.current !== null || trimmed === "") return false;
      if (auth.phase !== "ready") return false;
      const csrf = csrfRef.current;
      if (csrf === null) {
        setNotice(
          "当前页面没有 CSRF 凭证（cookie 只读态），不能发起变更请求；请用一键链接重新进入。",
        );
        return false;
      }
      setBusy(true);
      setNotice(null);
      try {
        const createdResponse = await fetch("/v1/threads", {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrf },
          body: JSON.stringify({}),
        });
        if (!createdResponse.ok) {
          throw new ApiError(createdResponse.status, await readErrorText(createdResponse));
        }
        const created = (await createdResponse.json()) as { id: string };
        // 先记录提交前游标（证据扫描起点），再落 pending，再发 POST：
        // 任何一步之后刷新都能恢复成「查询」而不是重复提交。
        let seen = 0;
        try {
          seen = readMaxSeq(await fetchEventFrames(created.id));
        } catch {
          seen = 0; // 游标读取失败只影响证据匹配精度，不阻断提交
        }
        const record: PendingTurn = {
          request_id: crypto.randomUUID(),
          thread_id: created.id,
          message: trimmed,
          mode,
          seen_seq: seen,
          started_at: new Date().toISOString(),
        };
        pendingRef.current = record;
        savePendingRecord(record);
        setPending(record);
        // 记入本浏览器线程记忆（侧栏「工作会话」数据源；title 取任务首行，
        // 与旧 Composer 的 rememberThread 口径一致）。
        rememberThread(created.id, trimmed.slice(0, 80));
        selectThread(created.id);
        try {
          const result = await postTurnIdempotent(record, csrf);
          clearPending(record);
          setNotice(`turn 完成（${result.total_tokens} tokens）。`);
          return true;
        } catch (cause) {
          if (cause instanceof ApiError && cause.status < 500) {
            // 服务端明确拒绝（4xx）：确定性终态，清除记录（kitagent command:314-316 同款）。
            clearPending(record);
            setNotice(`任务提交被拒绝：${cause.message}`);
          } else if (cause instanceof ApiError) {
            setNotice(`服务端错误（HTTP ${cause.status}）：结果未确认，已记录待查。`);
          } else {
            setNotice(
              `网络错误：结果未确认，已记录待查（不会自动重发）：${cause instanceof Error ? cause.message : String(cause)}`,
            );
          }
          return false;
        }
      } catch (cause) {
        setNotice(`创建线程失败：${cause instanceof Error ? cause.message : String(cause)}`);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [auth.phase, busy, clearPending, selectThread],
  );

  const dismissNotice = useCallback((): void => {
    setNotice(null);
  }, []);

  // ------------------------------------------------------------------
  // SSE 连接（since 游标 + 退避重连 + SequenceGap → since=0 全量回放）
  // ------------------------------------------------------------------
  useEffect(() => {
    if (auth.phase !== "ready" || threadId === null) {
      setWorkbench(null);
      setStreamState("connecting");
      return () => undefined;
    }
    let stopped = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let since = 0;
    let state = initialWorkbenchState(threadId);
    setWorkbench(state);
    setStreamState("connecting");
    setStreamError(null);
    // 连接代际号（2026-10-27 复审修复——SSE 僵尸流泄漏）：connect 每次自增；已落后
    // 代际的旧链路（超时重连定时器与被 abort 流的 catch 双触发过）只 abort 自己并
    // 退出，不得再建连接或调度重连。没有这道闸，一条链路会分裂成多条并发 SSE 流：
    // 每条流占死一个浏览器 per-host socket（SSE 响应永不结束），泄漏 6 条即耗尽
    // Chromium 连接池上限，页面此后任何导航（如 journey step10 的 reload）永久排队
    // ——这正是 journey 首跑 step10 reload 30s 超时、复跑随机的根因。
    let connectGen = 0;

    const clearRetryTimer = (): void => {
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
    };

    const scheduleReconnect = (reason: string): void => {
      if (stopped) return;
      setStreamState("reconnecting");
      setStreamError(reason);
      // 重设前必须清掉旧定时器：丢失句柄的旧定时器仍会触发，造成双连接。
      clearRetryTimer();
      retryTimer = setTimeout(connect, Math.min(500 * 2 ** attempt, 8000));
      attempt += 1;
    };

    /** 断档重拉快照：重建状态 + since=0 全量回放（kitagent publish 捕获异常同位）。 */
    const resync = (reason: string): void => {
      if (stopped) return;
      connectGen += 1; // 本链路作废：abort 后其 catch 落后代际，不再重复调度
      state = initialWorkbenchState(threadId);
      since = 0;
      setWorkbench(state);
      setStreamState("reconnecting");
      setStreamError(`${reason}；正在重拉快照。`);
      clearRetryTimer();
      controller?.abort();
      retryTimer = setTimeout(connect, Math.min(500 * 2 ** attempt, 8000));
      attempt += 1;
    };

    const applyFrame = (frame: SseFrame): void => {
      if (frame.id > since) since = frame.id;
      try {
        state = applyEvent(state, frame);
      } catch (cause) {
        if (cause instanceof SequenceGap) {
          resync(cause.message);
          return;
        }
        throw cause;
      }
      attempt = 0;
      setStreamState("live");
      setWorkbench(state);
    };

    const connect = (): void => {
      if (stopped) return;
      clearRetryTimer();
      const gen = ++connectGen;
      const ctl = new AbortController();
      controller = ctl;
      fetch(`/v1/threads/${encodeURIComponent(threadId)}/events/stream?since=${since}`, {
        signal: ctl.signal,
      })
        .then(async (response) => {
          if (stopped || gen !== connectGen) {
            ctl.abort();
            return;
          }
          if (response.status === 404) {
            // 线程不存在（实例已重启/清库）：终态错误，不无限重连。
            setStreamState("error");
            setStreamError("线程不存在或已随实例重置。");
            return;
          }
          if (!response.ok || response.body === null) {
            throw new Error(`HTTP ${response.status}`);
          }
          // U3：连接（重）建立即清错——重连原因是瞬态，不得在 error-banner 常驻
          // （空闲期无帧可达，清错必须挂在建连而非收帧）；终态 404 在上方提前
          // 返回，错误保持呈现。
          setStreamError(null);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done || stopped || gen !== connectGen) break;
            buffer += decoder.decode(value, { stream: true });
            const boundary = buffer.lastIndexOf("\n\n");
            if (boundary < 0) continue;
            const complete = buffer.slice(0, boundary + 2);
            buffer = buffer.slice(boundary + 2);
            for (const frame of parseSseFrames(complete)) {
              applyFrame(frame);
              if (stopped || gen !== connectGen) return;
            }
          }
          if (!stopped && gen === connectGen) scheduleReconnect("连接关闭");
        })
        .catch((cause: unknown) => {
          if (stopped || gen !== connectGen) return;
          scheduleReconnect(cause instanceof Error ? cause.message : String(cause));
        });
    };

    connect();
    return () => {
      stopped = true;
      controller?.abort();
      if (retryTimer !== null) clearTimeout(retryTimer);
    };
  }, [auth.phase, threadId]);

  return {
    auth,
    threadId,
    selectThread,
    workbench,
    streamState,
    streamError,
    pending,
    notice,
    busy,
    startTurn,
    redeem,
    checkPending,
    leavePending,
    dismissNotice,
  };
}
