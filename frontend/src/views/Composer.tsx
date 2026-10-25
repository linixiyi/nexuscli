import { useRef, useState, type JSX } from "react";
import { Send } from "lucide-react";

// Composer（2026-10-26_workbench-kitagent-ui task_0004，交互逻辑 Ported from
// kitagent (MIT, zed123214/kitagent) frontend/src/App.tsx Chat 的 composer：
// Enter 发送、Shift+Enter 换行、IME 合成保护、发送由父级 ChatPanel 编排）。
// 差异：本仓无模型元数据端点，footer 左侧为模式选择（react/plan/team）而非模型点。

export type ComposerMode = "react" | "plan" | "team";

export const MAX_MESSAGE_BYTES = 32768;

type Props = {
  draft: string;
  mode: ComposerMode;
  busy: boolean;
  canPost: boolean;
  disabledHint: string | null;
  onDraftChange: (text: string) => void;
  onModeChange: (mode: ComposerMode) => void;
  /** 已通过校验的提交；字节上限校验在本地（32KiB，kitagent 同款）。 */
  onSend: () => void;
};

export function Composer({
  draft,
  mode,
  busy,
  canPost,
  disabledHint,
  onDraftChange,
  onModeChange,
  onSend,
}: Props): JSX.Element {
  const [error, setError] = useState("");
  const composing = useRef(false);
  const lastDraft = useRef(draft);

  const send = (): void => {
    lastDraft.current = draft;
    if (!draft.trim() || busy || !canPost) return;
    if (new TextEncoder().encode(draft).length > MAX_MESSAGE_BYTES) {
      setError(`单条消息不能超过 ${Math.round(MAX_MESSAGE_BYTES / 1024)} KiB。`);
      return;
    }
    setError("");
    onSend();
  };

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <textarea
        aria-label="输入任务"
        placeholder={disabledHint ?? "描述你希望完成的任务…"}
        value={draft}
        rows={3}
        onChange={(event) => onDraftChange(event.target.value)}
        onCompositionStart={() => {
          composing.current = true;
        }}
        onCompositionEnd={() => {
          composing.current = false;
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && !composing.current) {
            event.preventDefault();
            send();
          }
        }}
      />
      <div className="composer-footer">
        <div className="composer-context">
          <select
            className="mode-select"
            aria-label="执行模式"
            value={mode}
            onChange={(event) => onModeChange(event.target.value as ComposerMode)}
          >
            <option value="react">react</option>
            <option value="plan">plan</option>
            <option value="team">team</option>
          </select>
          {/* 只读态说明走 disabledHint（占位符），这里不再重复渲染，避免
              「pending 未决」被误述成「无 CSRF 凭证」。 */}
        </div>
        <button className="send-button" type="submit" aria-label="发送任务" disabled={!draft.trim() || busy || !canPost}>
          <Send size={16} />
        </button>
      </div>
      {error !== "" && <p className="error-text composer-error">{error}</p>}
    </form>
  );
}
