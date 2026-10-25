import { memo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";

// Markdown 渲染（2026-10-26_workbench-kitagent-ui task_0004，Ported from kitagent
// (MIT, zed123214/kitagent) frontend/src/App.tsx:58-99 的 CodeBlock/Markdown）：
// 跳过 HTML、GFM 表格、代码块带复制按钮、外链新窗打开、不加载外部图片。

function CodeBlock({ children }: { children?: ReactNode }) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  return (
    <div className="code-block">
      <button
        className="copy-code"
        aria-label="复制代码"
        onClick={() => {
          void navigator.clipboard
            .writeText(ref.current?.textContent || "")
            .then(() => setCopied(true));
        }}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
      <pre ref={ref}>{children}</pre>
    </div>
  );
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        components={{
          pre: CodeBlock,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: ({ alt }) => <span>[图片：{alt || "不加载外部图片"}]</span>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
