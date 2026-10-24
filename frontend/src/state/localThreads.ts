// 本浏览器创建的线程记忆与会话 API key 的 localStorage 读写。

export type LocalThread = {
  id: string;
  title: string;
  createdAt: number;
  lastTurnAt: number;
};

const THREADS_KEY = "nexuscli.workbench.threads";

export function loadLocalThreads(): LocalThread[] {
  const raw = localStorage.getItem(THREADS_KEY);
  if (raw === null) {
    return [];
  }
  try {
    const parsed = JSON.parse(raw) as LocalThread[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function rememberThread(id: string, title: string): void {
  const threads = loadLocalThreads().filter((thread) => thread.id !== id);
  threads.unshift({ id, title, createdAt: Date.now(), lastTurnAt: Date.now() });
  localStorage.setItem(THREADS_KEY, JSON.stringify(threads.slice(0, 50)));
}
