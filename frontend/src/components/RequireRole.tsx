import { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "../auth";
import type { Role } from "../auth";

/**
 * 路由级守卫：权限不够就弹回首页，而不是渲染一个报错的空壳。
 *
 * 在此之前 `/settings/*` 只靠 `SettingsLayout` 把标签藏起来，但 `<Outlet />`
 * 照常渲染子路由——只读账号直接敲 URL 就能看到管理员页面的骨架（表单、按钮、
 * 一片 403 报错）。后端三个接口都返 403，所以**没有数据泄露**，问题是它让
 * 用户以为自己该有这些权限，也让页面看起来是坏的。
 *
 * 这只是体验层。真正的拦截在后端（spec §8.1），前端判断永远只用于隐藏入口。
 */
export function RequireRole({
  allow,
  children,
}: {
  allow: (role: Role | undefined) => boolean;
  children: ReactNode;
}) {
  const { user } = useAuth();
  if (!allow(user?.role)) return <Navigate to="/" replace />;
  return <>{children}</>;
}
