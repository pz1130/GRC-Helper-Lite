import fs from "node:fs";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const isDocker = fs.existsSync("/.dockerenv");
const target = process.env.VITE_API_TARGET || (isDocker ? "http://api:8000" : "http://localhost:8000");

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 开发时前端直连 api 服务，自动区分 Docker 容器环境与宿主机环境
      "/api": { target, changeOrigin: true },
    },
  },
});
