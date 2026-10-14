import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../../api";
import type { Role } from "../../auth";
import { Card, DataTable } from "../../components/ui";

interface UserRow {
  id: number;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
  expires_at: string | null;
}

const ROLES: Role[] = ["admin", "grc_lead", "contributor", "viewer"];

/**
 * 表单上的「有效期至」是**含当天**的：选 10 月 1 日，这个账号 10 月 1 日全天可用，
 * 管理员所在时区的当天结束后失效（2026-09-23 决定）。
 *
 * 原来直接把 "2026-10-01" 交给后端，存成 UTC 零点——北京时间当天早上 8 点就进不来，
 * 纽约则是前一天晚上 8 点，同一个日期在不同地方意味着不同的时刻。
 */
function endOfLocalDay(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  return new Date(year, month - 1, day, 23, 59, 59, 999).toISOString();
}

/** 按本地时区显示日期：存的是本地当天的最后一刻，UTC 日期可能已经是第二天。 */
function localDate(iso: string): string {
  const at = new Date(iso);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`;
}

export function Users() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    email: "",
    name: "",
    role: "viewer" as Role,
    password: "",
    expires_at: "",
  });
  // 同一时刻只开一行的重设表单；密码只在这里停留，发出去就清掉。
  const [resetting, setResetting] = useState<{ id: number; password: string } | null>(null);
  const [notice, setNotice] = useState("");

  const users = useQuery({
    queryKey: ["users"],
    queryFn: () => request<UserRow[]>("/api/users"),
  });

  const create = useMutation({
    mutationFn: () =>
      request<UserRow>("/api/users", {
        method: "POST",
        body: JSON.stringify({ ...form, expires_at: form.expires_at ? endOfLocalDay(form.expires_at) : null }),
      }),
    onError: (e: Error) => setError(e.message),
    onSuccess: () => {
      setError("");
      setForm({ ...form, email: "", name: "", password: "", expires_at: "" });
      void queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });

  const update = useMutation({
    mutationFn: (body: { id: number; patch: Partial<UserRow> }) =>
      request<UserRow>(`/api/users/${body.id}`, {
        method: "PATCH",
        body: JSON.stringify(body.patch),
      }),
    onError: (e: Error) => setError(e.message),
    onSuccess: () => {
      setError("");
      void queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });

  // 单独一条 mutation 且只发 password：带上行里的其他字段会把别人刚改的角色覆盖回去。
  const resetPassword = useMutation({
    mutationFn: (body: { id: number; email: string; password: string }) =>
      request<UserRow>(`/api/users/${body.id}`, {
        method: "PATCH",
        body: JSON.stringify({ password: body.password }),
      }),
    onError: (e: Error) => {
      setNotice("");
      setError(e.message);
    },
    onSuccess: (_, body) => {
      setError("");
      setResetting(null);
      setNotice(t("settings.usersConfig.passwordReset", { email: body.email }));
    },
  });

  function submitReset(event: FormEvent, user: UserRow) {
    event.preventDefault();
    if (resetting) resetPassword.mutate({ id: user.id, email: user.email, password: resetting.password });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <div>
      {/* Users Table Card */}
      <Card className="kn-mb-6" title={t("settings.users")}>
        {error && (
          <p role="alert">
            <span>⚠️</span> {error}
          </p>
        )}
        {notice && (
          <p role="status">
            <span>✅</span> {notice}
          </p>
        )}
        {users.error && (
          <p role="alert">
            <span>⚠️</span> {users.error.message}{" "}
            <button className="kn-btn-secondary kn-btn-sm" onClick={() => void users.refetch()}>
              {t("common.retry")}
            </button>
          </p>
        )}
        <DataTable className="kn-m-0" head={<><tr>
                <th>{t("settings.usersConfig.email")}</th>
                <th>{t("settings.usersConfig.name")}</th>
                <th>{t("settings.usersConfig.role")}</th>
                <th>{t("settings.usersConfig.active")}</th>
                <th>{t("settings.usersConfig.expiresAt")}</th>
                <th>{t("common.actions")}</th>
              </tr></>}>
              {users.data?.map((u) => (
                <Fragment key={u.id}>
                <tr>
                  <td className="kn-strong kn-primary-text">{u.email}</td>
                  <td>{u.name}</td>
                  <td>
                    <select
                      aria-label={t("settings.usersConfig.roleOf", { email: u.email })}
                      value={u.role}
                      onChange={(e) =>
                        update.mutate({ id: u.id, patch: { role: e.target.value as Role } })
                      }
                      className="kn-select-xs"
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {t(`settings.usersConfig.roles.${r}`)}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={t("settings.usersConfig.activeOf", { email: u.email })}
                      checked={u.is_active}
                      onChange={(e) => update.mutate({ id: u.id, patch: { is_active: e.target.checked } })}
                    />
                  </td>
                  <td>
                    <span className="kn-secondary kn-text-xs">
                      {u.expires_at ? localDate(u.expires_at) : "—"}
                    </span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="kn-btn-secondary kn-btn-sm"
                      aria-label={t("settings.usersConfig.resetPasswordOf", { email: u.email })}
                      onClick={() => {
                        setNotice("");
                        setError("");
                        setResetting({ id: u.id, password: "" });
                      }}
                    >
                      {t("settings.usersConfig.resetPassword")}
                    </button>
                  </td>
                </tr>
                {resetting?.id === u.id && (
                  <tr>
                    <td colSpan={6}>
                      <form
                        onSubmit={(event) => submitReset(event, u)} className="kn-row kn-wrap"
                      >
                        <input
                          type="password"
                          autoComplete="new-password"
                          aria-label={t("settings.usersConfig.newPasswordOf", { email: u.email })}
                          placeholder={t("settings.usersConfig.newPasswordPlaceholder")}
                          value={resetting.password}
                          required
                          minLength={8}
                          autoFocus
                          onChange={(e) => setResetting({ id: u.id, password: e.target.value })}
                        />
                        <button type="submit" className="kn-btn-primary kn-btn-sm" disabled={resetPassword.isPending}>
                          {t("settings.usersConfig.setPassword")}
                        </button>
                        <button type="button" className="kn-btn-secondary kn-btn-sm" onClick={() => setResetting(null)}>
                          {t("common.cancel")}
                        </button>
                      </form>
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
            </DataTable>
      </Card>

      {/* Add User Card */}
      <div className="kn-max-w-md kn-card">
        <h4 className="kn-m-0 kn-mb-1 kn-text-md">{t("common.add")}</h4>
        <p className="kn-secondary kn-text-xs kn-mb-4">
          {t("settings.usersConfig.auditorHint")}
        </p>
        <form onSubmit={submit} className="kn-stack-3">
          <input
            type="email"
            placeholder={t("settings.usersConfig.email")}
            value={form.email}
            required
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
          <input
            placeholder={t("settings.usersConfig.name")}
            value={form.name}
            required
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
          <select
            aria-label={t("settings.usersConfig.newUserRole")}
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {t(`settings.usersConfig.roles.${r}`)}
              </option>
            ))}
          </select>
          <input
            type="password"
            placeholder={t("settings.usersConfig.passwordPlaceholder")}
            value={form.password}
            required
            minLength={8}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
          />
          <input
            type="date"
            aria-label={t("settings.usersConfig.expiresAt")}
            value={form.expires_at}
            onChange={(e) => setForm({ ...form, expires_at: e.target.value })}
          />
          <div>
            <button type="submit" className="kn-btn-wide kn-btn-primary">
              {t("common.save")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
