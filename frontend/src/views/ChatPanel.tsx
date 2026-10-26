import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import {
  ArrowDown,
  ArrowRight,
  FileText,
  Hourglass,
  MessageSquare,
  Radio,
  Sparkles,
  SquareTerminal,
} from "lucide-react";

import type { WorkbenchEvent, WorkbenchState } from "../state";
import { Composer, type ComposerMode } from "./Composer";
import { Markdown } from "../components/Markdown";

// 聊天主面板（2026-10-26_workbench-kitagent-ui task_0004）。消息组装基于
// state.ts 的语义事件流（G16 映射 turn.started 后用户消息可从回放重建）；
// 滚动跟随/草稿持久化/new-content Ported from kitagent (MIT, zed123214/kitagent)
// frontend/src/App.tsx Chat（follow ref + sessionStorage draft + revision 防抖）。

type ChatMessage =
  | { role: "user"; key: string; text: string }
  | { role: "assistant"; key: string; text: string; open: boolean };

export function deriveChatMessages(events: WorkbenchEvent[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  let turn = 0;
  for (const event of events) {
    if (event.kind === "turn.started") {
      turn += 1;
      messages.push({ role: "user", key: `user:${event.seq}`, text: event.message });
    } else if (event.kind === "text_delta") {
      const last = messages[messages.length - 1];
      if (last !== undefined && last.role === "assistant" && last.open) {
        last.text += event.text;
      } else {
        messages.push({ role: "assistant", key: `assistant:${turn}:${event.seq}`, text: event.text, open: true });
      }
    } else if (event.kind === "turn.completed") {
      const last = messages[messages.length - 1];
      if (last !== undefined && last.role === "assistant" && last.open) {
        // turn.completed 的 text 是服务端落库的完整正文（终态兜底，防 delta 丢帧）。
        last.text = event.text;
        last.open = false;
      }
    }
  }
  return messages;
}

const EXAMPLE_TASK = "检查样例 Python 仓库，执行检查脚本并生成检查报告。";

// W1（2026-10-27_workbench-ux-round2）慢等待反馈：busy 且 SSE 事件计数停滞超过阈值时，
// run-inline 下追加一行渐进提示；不改变 busy 语义、不自动取消。事件推进（events.length
// 变化）即重置计时——SSE 心跳是注释行、不入 events（state.ts applyEvent），不会误重置。
// 阈值默认 30s；真等 30s 对 e2e 太久，故可经 localStorage 键 `nexuscli:slow-hint-ms`
// （正整数毫秒）注入，e2e 写 200 后 reload 再发任务即可触发（demo 链 write_file 审批前
// 的空窗必然超过 200ms）。组件挂载时读取一次。
const SLOW_HINT_STORAGE_KEY = "nexuscli:slow-hint-ms";
const SLOW_HINT_DEFAULT_MS = 30_000;

function readSlowHintMs(): number {
  try {
    const raw = window.localStorage.getItem(SLOW_HINT_STORAGE_KEY);
    const parsed = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : SLOW_HINT_DEFAULT_MS;
  } catch {
    return SLOW_HINT_DEFAULT_MS; // localStorage 不可用（隐私模式等）→ 默认阈值
  }
}

type Props = {
  workbench: WorkbenchState | null;
  busy: boolean;
  canPost: boolean;
  disabledHint: string | null;
  /** U4（2026-10-27_workbench-ux-iteration）：存在挂起审批（approvals 轮询非空）。 */
  waitingApproval: boolean;
  onSend: (message: string, mode: ComposerMode) => Promise<boolean>;
};

export function ChatPanel({ workbench, busy, canPost, disabledHint, waitingApproval, onSend }: Props): JSX.Element {
  const threadId = workbench?.threadId ?? "";
  const draftKey = `nexuscli:draft:${threadId || "new"}`;
  const [draft, setDraft] = useState(() => sessionStorage.getItem(draftKey) ?? "");
  const [mode, setMode] = useState<ComposerMode>("react");
  const draftRevision = useRef(0);
  const [newContent, setNewContent] = useState(false);
  const scrolling = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  function editDraft(text: string): void {
    draftRevision.current += 1;
    setDraft(text);
    sessionStorage.setItem(draftKey, text);
  }

  // 切线程：恢复该线程草稿与滚动位置（kitagent Chat 同款）。
  useEffect(() => {
    setDraft(sessionStorage.getItem(draftKey) ?? "");
    follow.current = true;
    setNewContent(false);
    const saved = sessionStorage.getItem(`nexuscli:scroll:${threadId}`);
    if (saved && scrolling.current !== null) {
      scrolling.current.scrollTop = Number(saved);
      follow.current =
        scrolling.current.scrollHeight - scrolling.current.scrollTop - scrolling.current.clientHeight < 80;
    }
    // draftKey/threadId 同步切换；sessionStorage 读取只在切线程时执行。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  const messages = useMemo(
    () => (workbench === null ? [] : deriveChatMessages(workbench.events)),
    [workbench],
  );

  // 内容推进时：贴底则跟随，否则提示有新内容（kitagent Chat 同款）。
  const eventCount = workbench?.events.length ?? 0;
  useEffect(() => {
    const element = scrolling.current;
    if (element === null) return;
    if (follow.current) {
      element.scrollTop = element.scrollHeight;
    } else {
      setNewContent(true);
    }
  }, [eventCount]);

  // W1：busy 期间每次事件推进都重新起一个阈值计时器；到点仍无新事件 → 慢等待提示态。
  const [slowHintMs] = useState(readSlowHintMs);
  const [slowWaiting, setSlowWaiting] = useState(false);
  useEffect(() => {
    setSlowWaiting(false);
    if (!busy) return;
    const timer = window.setTimeout(() => setSlowWaiting(true), slowHintMs);
    return () => window.clearTimeout(timer);
  }, [busy, eventCount, slowHintMs]);

  const send = (): void => {
    const text = draft;
    const submittedRevision = draftRevision.current;
    void onSend(text, mode).then((sent) => {
      if (sent && mounted.current && draftRevision.current === submittedRevision) {
        setDraft("");
        sessionStorage.removeItem(draftKey);
        follow.current = true;
      }
    });
  };

  return (
    <section className="chat-panel">
      <div className="conversation-heading">
        <span>
          <MessageSquare size={16} /> 对话
        </span>
        <span className="muted">{messages.length > 0 ? "任务与公开回复" : "从一个具体任务开始"}</span>
      </div>
      <div
        className="messages"
        ref={scrolling}
        onScroll={(event) => {
          const element = event.currentTarget;
          follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
          sessionStorage.setItem(`nexuscli:scroll:${threadId}`, String(element.scrollTop));
          if (follow.current) setNewContent(false);
        }}
      >
        {messages.length === 0 ? (
          <div className="welcome">
            <div className="welcome-mark">
              <SquareTerminal size={29} />
            </div>
            <p className="eyebrow">YOUR AGENT, UNDER CONTROL</p>
            <h1>
              把任务交给 Agent，
              <br />
              把执行过程留在眼前。
            </h1>
            <p>
              从目标到工具调用，再到可核对的结果。
              <br />
              需要你的决定时，工作台会明确提示。
            </p>
            {canPost && (
              <button
                className="example-task"
                onClick={() => {
                  editDraft(EXAMPLE_TASK);
                }}
              >
                <FileText size={18} />
                <span>
                  <strong>检查样例仓库</strong>
                  <small>读取说明 · 执行检查 · 交付报告</small>
                </span>
                <ArrowRight size={16} />
              </button>
            )}
          </div>
        ) : (
          messages.map((message) =>
            message.role === "user" ? (
              <div className="user-message" key={message.key}>
                <div className="message-label">你</div>
                <div>{message.text}</div>
              </div>
            ) : (
              <div className="assistant-message" key={message.key}>
                <div className="message-label">
                  <span className="mini-logo">
                    <Sparkles size={13} />
                  </span>
                  NexusCLI
                </div>
                <Markdown text={message.text} />
              </div>
            ),
          )
        )}
        {busy && (
          <div className="run-inline running">
            <Radio size={14} className="spin" />
            {/* U4：审批挂起期 turn 仍在途（busy 一刀切），但行动指令应指向审批，
                不能谎报「执行中」（kitagent 有 approval 态区分）。 */}
            <span>{waitingApproval ? "等待审批 · 请在右侧面板批准" : "执行中"}</span>
            <span className="muted">工具调用与审批在右侧面板</span>
          </div>
        )}
        {/* 慢等待提示只在真等模型时出现：审批挂起期的阻塞源是用户决策，
            两套等待文案并存会自相矛盾（round2 复核观察）。 */}
        {busy && slowWaiting && !waitingApproval && (
          <div className="run-inline" data-testid="slow-wait-hint">
            <Hourglass size={14} />
            <span>仍在等待模型响应…（网络慢或模型负载高时可另开会话）</span>
          </div>
        )}
      </div>
      {newContent && (
        <button
          className="new-content"
          onClick={() => {
            follow.current = true;
            scrolling.current?.scrollTo({ top: scrolling.current.scrollHeight });
            setNewContent(false);
          }}
        >
          <ArrowDown size={14} /> 有新内容
        </button>
      )}
      <Composer
        draft={draft}
        mode={mode}
        busy={busy}
        canPost={canPost}
        disabledHint={disabledHint}
        onDraftChange={editDraft}
        onModeChange={setMode}
        onSend={send}
      />
      <div className="input-hint">
        Enter 发送 · Shift + Enter 换行 <span>执行中可编辑下一条草稿</span>
      </div>
    </section>
  );
}
