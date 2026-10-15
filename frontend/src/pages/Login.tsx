import { lazy, Suspense, useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api";
import { useAuth } from "../auth";
import { BrandLogo, BrandWordmark } from "../components/BrandLogo";
import { useTheme } from "../theme";
import { wantsHero3d } from "../three/support";

// three.js 很大：登录页的品牌视觉按需加载，失败或无 WebGL 时退回纯 SVG 示意。
const HeroScene = lazy(() => import("../three/HeroScene"));

export function Login() {
  const { t } = useTranslation();
  const { login, enterDemo } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  // 与总览 hero 同一套规则：有 WebGL 且动效档位为 full 才用 3D；自动化浏览器默认 CSS 版。
  const hero3d = wantsHero3d();

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
    <div className="login">
      <aside className="login-brand" aria-hidden="true">
        <div className="login-brand-glow" />
        <div className="login-brand-mark">
          <BrandLogo size={72} />
          <span className="login-brand-title">
            <b>GRC</b> Helper
          </span>
          <span className="login-brand-tag">{t("login.subtitle")}</span>
        </div>
        <div className="login-brand-stage">
          {hero3d ? (
            <Suspense fallback={<LoginLayers />}>
              <HeroScene className="login-hero-3d" />
            </Suspense>
          ) : (
            <LoginLayers />
          )}
        </div>
        <ul className="login-brand-points">
          <li>
            <span className="login-dot login-dot--blue" />
            Multi-standard mapping
          </li>
          <li>
            <span className="login-dot login-dot--emerald" />
            Evidence validity tracking
          </li>
          <li>
            <span className="login-dot login-dot--amber" />
            AI cost &amp; audit intelligence
          </li>
        </ul>
      </aside>

      <main className="login-main">
        <div className="login-float">
          <button
            type="button"
            onClick={toggleTheme}
            className="kn-btn-secondary kn-btn-sm kn-icon-btn"
            title={theme === "dark" ? t("app.themeLight") : t("app.themeDark")}
          >
            {theme === "dark" ? (
              <>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
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
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                </svg>
                <span>{t("app.themeDark")}</span>
              </>
            )}
          </button>
        </div>

        <div className="login-card">
          <div className="login-card-brand">
            <BrandWordmark mark={true} size={40} sub={t("login.subtitle")} />
          </div>

          <form onSubmit={submit} className="login-form">
            <div className="login-heading">
              <h1>{t("login.title")}</h1>
              <p className="login-notice">{t("preview.notice")}</p>
            </div>

            <label>
              {t("login.email")}
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="username"
                placeholder="name@organization.com"
                className="kn-w-full"
              />
            </label>

            <label>
              {t("login.password")}
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                placeholder="••••••••"
                className="kn-w-full"
              />
            </label>

            {error && (
              <p role="alert">
                <span>⚠️</span> {error}
              </p>
            )}

            <button type="submit" className="kn-btn-primary login-submit">
              {t("login.submit")}
            </button>

            <div className="login-demo">
              <div>
                <div className="login-demo-title">Offline demo</div>
                <span className="login-demo-hint">Explore the interface with sample data</span>
              </div>
              <button type="button" onClick={enterDemo} className="kn-btn-secondary kn-btn-sm login-demo-go">
                Enter demo ➜
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
}

/** 无 WebGL / 减少动态效果时的品牌示意：三层半透明平面，与总览 hero 同语义。 */
function LoginLayers() {
  return (
    <div className="login-layers">
      <span className="login-layer login-layer--1" />
      <span className="login-layer login-layer--2" />
      <span className="login-layer login-layer--3" />
    </div>
  );
}
