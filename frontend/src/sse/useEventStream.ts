// useEventStream：SSE 事件流统一消费（01 契约 GET /v1/threads/{id}/events/stream）。
// - since 游标：初次 since=0 全量回放，之后手工 since=lastId 重连（浏览器 EventSource
//   带不了 x-api-key 头，重连必为手工——01「数据流·SSE 连接生命周期」定稿）
// - 错误分类：401/404/403 是终态错误（上层呈现），网络类错误经 N 次退避后降级轮询
// - 帧格式：id:/event:/data: 三段，空行分隔；: ping 心跳行被忽略

import { useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import type { SseFrame } from "../api/types";

export type StreamState = "connecting" | "live" | "polling" | "error";

const MAX_RETRIES = 3;
const RETRY_BACKOFF_MS = [1000, 2000, 4000];

function parseFrames(chunk: string): SseFrame[] {
  // 只解析完整帧（以空行结尾的 chunk 由调用方保证）；data 非法 JSON 时保留原文不炸 UI。
  const frames: SseFrame[] = [];
  let current: { id?: number; event?: string; data?: string } = {};
  let sawLine = false;
  for (const line of chunk.split("\n")) {
    if (line === "") {
      if (sawLine && current.event !== undefined && current.data !== undefined) {
        try {
          frames.push({
            id: current.id ?? 0,
            event: current.event,
            data: JSON.parse(current.data) as Record<string, unknown>,
          });
        } catch {
          frames.push({ id: current.id ?? 0, event: current.event, data: { raw: current.data } });
        }
      }
      current = {};
      sawLine = false;
      continue;
    }
    if (line.startsWith(":")) {
      continue; // 心跳/注释行
    }
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

export function useEventStream(threadId: string | null): {
  frames: SseFrame[];
  state: StreamState;
  lastError: string | null;
} {
  const [frames, setFrames] = useState<SseFrame[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const [lastError, setLastError] = useState<string | null>(null);
  const stopRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    setFrames([]);
    setState("connecting");
    setLastError(null);
    if (threadId === null || threadId === "") {
      setState("error");
      setLastError("未选择线程");
      return () => stopRef.current?.();
    }

    let stopped = false;
    let controller: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let retries = 0;
    let lastId = 0;
    const localFrames: SseFrame[] = [];

    const appendFrame = (frame: SseFrame) => {
      localFrames.push(frame);
      if (frame.id > lastId) {
        lastId = frame.id;
      }
      setFrames([...localFrames]);
    };

    const stop = () => {
      stopped = true;
      controller?.abort();
      if (retryTimer !== null) {
        clearTimeout(retryTimer);
      }
    };
    stopRef.current = stop;

    const openStream = () => {
      if (stopped) {
        return;
      }
      controller = new AbortController();
      // 手工 fetch 流式读取（EventSource 无法设置 x-api-key 头——01 契约的鉴权约束）
      // cookie 同源自动携带（HttpOnly）；api_key 查询参数仅为外部 curl/EventSource 保留
      const url =
        `/v1/threads/${encodeURIComponent(threadId)}/events/stream?since=${lastId}`;
      fetch(url, { signal: controller.signal })
        .then(async (response) => {
          if (response.status === 401 || response.status === 403 || response.status === 404) {
            setState("error");
            setLastError(`HTTP ${response.status}`);
            return;
          }
          if (!response.ok) {
            throw new ApiError(response.status, `HTTP ${response.status}`);
          }
          setState("live");
          retries = 0;
          const reader = response.body?.getReader();
          if (reader === undefined) {
            throw new Error("no stream body");
          }
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done || stopped) {
              break;
            }
            buffer += decoder.decode(value, { stream: true });
            // 只切分到最后一个空行边界，未完成的帧留在 buffer 等下一个读块
            const boundary = buffer.lastIndexOf("\n\n");
            if (boundary < 0) {
              continue;
            }
            const complete = buffer.slice(0, boundary + 2);
            buffer = buffer.slice(boundary + 2);
            for (const frame of parseFrames(complete)) {
              appendFrame(frame);
            }
          }
          if (!stopped) {
            scheduleReconnect("连接关闭");
          }
        })
        .catch((error: unknown) => {
          if (stopped) {
            return;
          }
          scheduleReconnect(error instanceof Error ? error.message : String(error));
        });
    };

    const scheduleReconnect = (reason: string) => {
      retries += 1;
      if (retries > MAX_RETRIES) {
        setState("polling");
        setLastError(`${reason}；已降级为轮询快照`);
        startPolling();
        return;
      }
      const delay = RETRY_BACKOFF_MS[Math.min(retries - 1, RETRY_BACKOFF_MS.length - 1)];
      setState("connecting");
      setLastError(reason);
      retryTimer = setTimeout(openStream, delay);
    };

    const startPolling = () => {
      const poll = async () => {
        if (stopped) {
          return;
        }
        try {
          const response = await fetch(
            `/v1/threads/${encodeURIComponent(threadId)}/events`,
          );
          if (response.ok) {
            const text = await response.text();
            for (const frame of parseFrames(text)) {
              if (frame.id > lastId) {
                appendFrame(frame);
              }
            }
          }
        } catch {
          // 轮询失败静默重试
        }
        retryTimer = setTimeout(poll, 2000);
      };
      poll();
    };

    openStream();
    return () => {
      stop();
      stopRef.current = null;
    };
  }, [threadId]);

  return { frames, state, lastError };
}
