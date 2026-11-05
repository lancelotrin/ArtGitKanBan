import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 构建产物放 dist/，由 server/app.py 在 /static/ 下提供；页面路径（/wish 等）返回 dist/index.html。
// 开发时 npm run dev，接口和登录都转给本机的 console.py（8765，它再转给 FastAPI）。
const CONSOLE = "http://127.0.0.1:8765";

export default defineConfig({
  base: "/static/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api/v1": CONSOLE, "/login": CONSOLE, "/auth": CONSOLE },
  },
});
