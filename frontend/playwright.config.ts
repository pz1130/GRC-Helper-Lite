import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  // 冒烟用例之间有先后依赖（先建 provider，后查审计日志），串行跑。
  workers: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
  },
});
