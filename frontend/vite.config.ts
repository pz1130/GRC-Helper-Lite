import fs from "node:fs";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const isDocker = fs.existsSync("/.dockerenv");
const target = process.env.VITE_API_TARGET || (isDocker ? "http://api:8000" : "http://localhost:8000");

export default defineConfig({
  plugins: [react()],
  build: {
    // 默认 500 kB 只会被 three 的包（约 521 kB）触发：它只在 3D 视图里按需加载，摇树后
    // 已接近 WebGLRenderer 的下限，警告每次都响就没人看了。560 仍拦得住真正要紧的回归：
    // 页面改回静态引入，入口包会从约 333 kB 涨回 670 kB；three 被打进入口则超过 850 kB。
    chunkSizeWarningLimit: 560,
  },
  server: {
    port: 5173,
    proxy: {
      // 开发时前端直连 api 服务，自动区分 Docker 容器环境与宿主机环境
      "/api": { target, changeOrigin: true },
    },
  },
});
