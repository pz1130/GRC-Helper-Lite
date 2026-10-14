import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { SignalParticlesCanvas } from "../components/SignalParticlesCanvas";

import { ApiError } from "../api";
import { useAuth } from "../auth";
import { useTheme } from "../theme";
import { BrandLogo } from "../components/BrandLogo";

export function Login() {
  const { t } = useTranslation();
  const { login, enterDemo } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await login(email, password);
    } catch (caught) {
      // 账号本身的问题后端一律回 401「邮箱或密码不正确」（不暴露邮箱是否存在），照旧。
      // 其余不能也说成密码错：被限流的人会继续重试、一直被挡；服务宕机时所有人都以为
      // 自己密码错了。
      if (caught instanceof ApiError && caught.status === 401) setError(t("login.failed"));
      else if (caught instanceof ApiError) setError(caught.message);
      else setError(t("common.requestFailed"));
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
        position: "relative",
        overflow: "hidden",
      }}
    >
      {/* 原生纯 Canvas 信号粒子背景：0 iframe、0 CDN、纯离线高性能渲染 */}
      <div
        className="shader-frame"
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          zIndex: 0,
          overflow: "hidden",
        }}
      >
        <SignalParticlesCanvas
          mode={theme === "light" ? "light" : "dark"}
          speed={1.0}
        />
      </div>
      {/* Floating Controls: Theme */}
      <div style={{ position: "fixed", top: 20, right: 24, zIndex: 10, display: "flex", alignItems: "center", gap: 8 }}>
        <button
          type="button"
          onClick={toggleTheme}
          className="kn-btn-secondary kn-btn-sm"
          style={{ display: "inline-flex", alignItems: "center", gap: 7 }}
          title={theme === "dark" ? t("app.themeLight") : t("app.themeDark")}
        >
          {theme === "dark" ? (
            <>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5" />
                <line x1="12" y1="1" x2="12" y2="3" />
                <line x1="12" y1="21" x2="12" y2="23" />
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
                <line x1="1" y1="12" x2="3" y2="12" />
                <line x1="21" y1="12" x2="23" y2="12" />
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
              </svg>
              <span>{t("app.themeLight")}</span>
            </>
          ) : (
            <>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
              </svg>
              <span>{t("app.themeDark")}</span>
            </>
          )}
        </button>
      </div>

      <div
        className="kn-card"
        style={{
          width: "100%",
          maxWidth: 420,
          padding: "40px 32px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          position: "relative",
          zIndex: 10,
          boxShadow: "0 20px 50px rgba(0, 0, 0, 0.4), 0 0 0 1px var(--stage-border)",
          backdropFilter: "blur(20px)",
          WebkitBackdropFilter: "blur(20px)",
        }}
      >
        <BrandLogo size={56} className="kn-mb-5" />

        <form onSubmit={submit} style={{ width: "100%", display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ textAlign: "center", marginBottom: 6 }}>
            <h1 style={{ fontSize: "1.75rem", margin: 0 }}>{t("login.title")}</h1>
            <p style={{ fontSize: "0.875rem", color: "var(--text-secondary)", marginTop: 6 }}>
              {t("login.subtitle")}
            </p>
          </div>

          <p style={{ color: "var(--text-secondary)", fontSize: "0.8rem" }}>{t("preview.notice")}</p>

          <label>
            {t("login.email")}
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="name@organization.com" className="kn-w-full"
            />
          </label>

          <label>
            {t("login.password")}
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              placeholder="••••••••" className="kn-w-full"
            />
          </label>

          {error && (
            <p role="alert">
              <span>⚠️</span> {error}
            </p>
          )}

          <button
            type="submit"
            className="kn-btn-primary"
            style={{ width: "100%", padding: "12px", marginTop: 8, fontSize: "0.9375rem" }}
          >
            {t("login.submit")}
          </button>

          <div
            style={{
              marginTop: 4,
              padding: "10px 14px",
              borderRadius: "var(--radius-sm)",
              background: "rgba(255, 255, 255, 0.03)",
              border: "1px dashed var(--stage-border)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              fontSize: "0.75rem",
              color: "var(--text-secondary)",
            }}
          >
            <div>
              <div style={{ color: "var(--text-primary)", fontWeight: 500, marginBottom: 2 }}>
                Offline demo
              </div>
              <code style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>Explore the interface with sample data</code>
            </div>
            <button
              type="button"
              onClick={enterDemo}
              className="kn-btn-secondary kn-btn-sm"
              style={{
                fontSize: "0.75rem",
                padding: "6px 12px",
                cursor: "pointer",
                fontWeight: 600,
                color: "var(--accent-primary)",
              }}
            >
              Enter demo ➜
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
