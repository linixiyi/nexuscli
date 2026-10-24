import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev 模式经同源 proxy 转发 /v1 到本机 Runtime API（默认 8080，可用
// NEXUSCLI_WORKBENCH_API_PORT 覆盖）；生产构建产物由 Runtime API 直接托管，
// 同源无 CORS 问题。
const apiPort = process.env.NEXUSCLI_WORKBENCH_API_PORT ?? "8080";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: true,
      },
    },
  },
});
