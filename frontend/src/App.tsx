import { lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { canManageLlmConfig, canReadAuditLog, canWriteEvidence, useAuth } from "./auth";
import { BrandLogo } from "./components/BrandLogo";
import { Layout } from "./components/Layout";
import { RequireRole } from "./components/RequireRole";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";

// 登录页和登录后的首页随主包加载，其余页面按路由拆包，进到那一页才下载。
// 以前全部静态引入，主包约 670 kB，第一次打开要把 25 个页面一起下完。
// Suspense 在 Layout 的 `<Outlet />` 外面，等待时外壳照常显示。
// 拆出去的包下载失败（断网、部署后旧哈希没了）会落到 PageErrorBoundary，
// 「重试」救不回来——React.lazy 会缓存失败的结果——刷新页面才行。
const Documents = lazy(() => import("./pages/Documents").then((m) => ({ default: m.Documents })));
const DocumentDetail = lazy(() => import("./pages/DocumentDetail").then((m) => ({ default: m.DocumentDetail })));
const ChangeImpact = lazy(() => import("./pages/ChangeImpact").then((m) => ({ default: m.ChangeImpact })));
const AuditLog = lazy(() => import("./pages/settings/AuditLog").then((m) => ({ default: m.AuditLog })));
const Providers = lazy(() => import("./pages/settings/Providers").then((m) => ({ default: m.Providers })));
const Redaction = lazy(() => import("./pages/settings/Redaction").then((m) => ({ default: m.Redaction })));
const EvidenceTypes = lazy(() => import("./pages/settings/EvidenceTypes").then((m) => ({ default: m.EvidenceTypes })));
const SettingsHome = lazy(() => import("./pages/settings/SettingsLayout").then((m) => ({ default: m.SettingsHome })));
const SettingsLayout = lazy(() => import("./pages/settings/SettingsLayout").then((m) => ({ default: m.SettingsLayout })));
const ThresholdsPage = lazy(() => import("./pages/settings/Thresholds").then((m) => ({ default: m.ThresholdsPage })));
const Users = lazy(() => import("./pages/settings/Users").then((m) => ({ default: m.Users })));
const Account = lazy(() => import("./pages/Account").then((m) => ({ default: m.Account })));
const Search = lazy(() => import("./pages/Search").then((m) => ({ default: m.Search })));
const ReviewQueue = lazy(() => import("./pages/ReviewQueue").then((m) => ({ default: m.ReviewQueue })));
const Controls = lazy(() => import("./pages/Controls").then((m) => ({ default: m.Controls })));
const ControlDetail = lazy(() => import("./pages/ControlDetail").then((m) => ({ default: m.ControlDetail })));
const Graph = lazy(() => import("./pages/graph/Graph").then((m) => ({ default: m.Graph })));
const Frameworks = lazy(() => import("./pages/Frameworks").then((m) => ({ default: m.Frameworks })));
const FrameworkDetail = lazy(() => import("./pages/FrameworkDetail").then((m) => ({ default: m.FrameworkDetail })));
const TechAssets = lazy(() => import("./pages/TechAssets").then((m) => ({ default: m.TechAssets })));
const Evidence = lazy(() => import("./pages/Evidence").then((m) => ({ default: m.Evidence })));
const AuditAssistant = lazy(() => import("./pages/AuditAssistant").then((m) => ({ default: m.AuditAssistant })));
const Maturity = lazy(() => import("./pages/Maturity").then((m) => ({ default: m.Maturity })));
const RiskRegister = lazy(() => import("./pages/RiskRegister").then((m) => ({ default: m.RiskRegister })));

export function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          gap: 16,
        }}
      >
        <BrandLogo size={48} className="kn-mb-1" />
        <p className="brand-boot-label">GRC HELPER</p>
      </div>
    );
  }
  if (!user) return <Login />;

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Overview />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/search" element={<Search />} />
        <Route path="/review" element={<ReviewQueue />} />
        <Route path="/controls" element={<Controls />} />
        <Route path="/controls/:id" element={<ControlDetail />} />
        <Route path="/graph" element={<Graph />} />
        <Route path="/frameworks" element={<Frameworks />} />
        <Route path="/frameworks/:id" element={<FrameworkDetail />} />
        <Route path="/tech-assets" element={<TechAssets />} />
        <Route path="/evidence" element={<Evidence />} />
        <Route path="/audit" element={<AuditAssistant />} />
        <Route path="/maturity" element={<Maturity />} />
        <Route path="/risks" element={<RiskRegister />} />
        <Route path="/account" element={<Account />} />
        <Route path="/documents/:id" element={<DocumentDetail />} />
        <Route path="/documents/:id/change-impact" element={<ChangeImpact />} />
        {/* 每个子路由各自守卫，不是一刀切：审计日志 grc_lead 也能看，
            证据类型 contributor 也能维护。守卫只负责不渲染空壳，
            真正的拦截在后端（spec §8.1）。 */}
        <Route path="/settings" element={<SettingsLayout />}>
          <Route index element={<SettingsHome />} />
          <Route path="providers" element={<RequireRole allow={canManageLlmConfig}><Providers /></RequireRole>} />
          <Route path="redaction" element={<RequireRole allow={canManageLlmConfig}><Redaction /></RequireRole>} />
          <Route path="thresholds" element={<RequireRole allow={canManageLlmConfig}><ThresholdsPage /></RequireRole>} />
          <Route path="users" element={<RequireRole allow={canManageLlmConfig}><Users /></RequireRole>} />
          <Route path="evidence-types" element={<RequireRole allow={canWriteEvidence}><EvidenceTypes /></RequireRole>} />
          <Route path="audit-log" element={<RequireRole allow={canReadAuditLog}><AuditLog /></RequireRole>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
