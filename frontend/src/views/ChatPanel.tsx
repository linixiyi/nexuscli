import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import { ArrowDown, ArrowRight, FileText, MessageSquare, Radio, Sparkles, SquareTerminal } from "lucide-react";

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
