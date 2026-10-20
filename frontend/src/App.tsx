import { Navigate, Route, Routes } from "react-router-dom";

import { canManageLlmConfig, canReadAuditLog, canWriteEvidence, useAuth } from "./auth";
import { BrandLogo } from "./components/BrandLogo";
import { Layout } from "./components/Layout";
import { RequireRole } from "./components/RequireRole";
import { MotionLab } from "./pages/MotionLab";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Documents } from "./pages/Documents";
import { DocumentDetail } from "./pages/DocumentDetail";
import { ChangeImpact } from "./pages/ChangeImpact";
import { AuditLog } from "./pages/settings/AuditLog";
import { Providers } from "./pages/settings/Providers";
import { Redaction } from "./pages/settings/Redaction";
import { EvidenceTypes } from "./pages/settings/EvidenceTypes";
import { SettingsHome, SettingsLayout } from "./pages/settings/SettingsLayout";
import { ThresholdsPage } from "./pages/settings/Thresholds";
import { Users } from "./pages/settings/Users";
import { Account } from "./pages/Account";
import { Search } from "./pages/Search";
import { ReviewQueue } from "./pages/ReviewQueue";
import { Controls } from "./pages/Controls";
import { ControlDetail } from "./pages/ControlDetail";
import { Graph } from "./pages/graph/Graph";
import { Frameworks } from "./pages/Frameworks";
import { FrameworkDetail } from "./pages/FrameworkDetail";
import { TechAssets } from "./pages/TechAssets";
import { Evidence } from "./pages/Evidence";
import { AuditAssistant } from "./pages/AuditAssistant";
import { Maturity } from "./pages/Maturity";
import { RiskRegister } from "./pages/RiskRegister";

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
        {/* 动效样本页：仅 ui-motion-samples 分支，不进导航 */}
        <Route path="/motion-lab" element={<MotionLab />} />
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
