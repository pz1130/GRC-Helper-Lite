import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { SignalParticlesCanvas } from "../components/SignalParticlesCanvas";

import { ApiError } from "../api";
import { useAuth } from "../auth";
import { useTheme } from "../theme";
import { BrandLogo } from "../components/BrandLogo";
import { Card } from "../components/ui";

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
    <div className="kn-login-page">
      {/* 原生纯 Canvas 信号粒子背景：0 iframe、0 CDN、纯离线高性能渲染 */}
      <div className="shader-frame kn-login-shader">
        <SignalParticlesCanvas
          mode={theme === "light" ? "light" : "dark"}
          speed={1.0}
        />
      </div>
      {/* Floating Controls: Theme */}
      <div className="kn-login-float">
        <button
          type="button"
          onClick={toggleTheme}
          className="kn-btn-secondary kn-btn-sm kn-icon-btn"
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

      <Card className="kn-login-card">
        <BrandLogo size={56} className="kn-mb-5" />

        <form onSubmit={submit} className="kn-login-form">
          <div className="kn-login-heading">
            <h1>{t("login.title")}</h1>
            <p>{t("login.subtitle")}</p>
          </div>

          <p className="kn-login-notice">{t("preview.notice")}</p>

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

          <button type="submit" className="kn-btn-primary kn-login-submit">
            {t("login.submit")}
          </button>

          <div className="kn-login-demo">
            <div>
              <div className="kn-login-demo-title">Offline demo</div>
              <code className="kn-login-demo-hint">Explore the interface with sample data</code>
            </div>
            <button type="button" onClick={enterDemo} className="kn-btn-secondary kn-btn-sm kn-login-demo-go">
              Enter demo ➜
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}
