import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AuthProvider } from "./auth";
import "./i18n";
import "./styles/keynote.css";
import "./styles/ledger.css";
import "./styles/shell.css";
import "./styles/brand.css";
import "./styles/motion.css";
import { initMotion } from "./motion";
import { ThemeProvider, applyTheme, getInitialTheme } from "./theme";

applyTheme(getInitialTheme());
initMotion();

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          {/* 筛选框直接由 URL 驱动，保持同步更新，避免路由过渡把勾选状态回退。 */}
          <BrowserRouter useTransitions={false}>
            <App />
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
);
