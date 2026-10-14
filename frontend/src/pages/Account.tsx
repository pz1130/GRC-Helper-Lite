import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { request, setToken } from "../api";
import { useAuth } from "../auth";
import { Card, PageHeader } from "../components/ui";

interface PasswordChangeOut {
  access_token: string;
}

const EMPTY = { current: "", next: "", confirm: "" };

/**
 * 自己改密码。后端改完会让这个账号已经发出去的令牌全部作废，并回一张新令牌——
 * 这个会话换上新令牌继续用，其他设备上的会话需要重新登录。
 */
export function Account() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const change = useMutation({
    mutationFn: () =>
      request<PasswordChangeOut>("/api/auth/password", {
        method: "POST",
        body: JSON.stringify({ current_password: form.current, new_password: form.next }),
      }),
    onError: (e: Error) => {
      setNotice("");
      setError(e.message);
    },
    onSuccess: (result) => {
      setToken(result.access_token);
      setForm(EMPTY);
      setError("");
      setNotice(t("account.passwordChanged"));
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setNotice("");
    if (form.next !== form.confirm) {
      setError(t("account.passwordMismatch"));
      return;
    }
    setError("");
    change.mutate();
  }

  return (
    <div className="kn-max-w-md">
      <PageHeader title={t("account.title")} subtitle={user?.email} />

      <Card>
        <h3 className="kn-card-title-sm">{t("account.changePassword")}</h3>
        <p className="kn-secondary kn-text-xs kn-mb-4">
          {t("account.changePasswordHint")}
        </p>
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
        <form onSubmit={submit} className="kn-stack-3">
          <label>
            {t("account.currentPassword")}
            <input
              type="password"
              autoComplete="current-password"
              value={form.current}
              required
              onChange={(e) => setForm({ ...form, current: e.target.value })}
            />
          </label>
          <label>
            {t("account.newPassword")}
            <input
              type="password"
              autoComplete="new-password"
              value={form.next}
              required
              minLength={8}
              onChange={(e) => setForm({ ...form, next: e.target.value })}
            />
          </label>
          <label>
            {t("account.confirmPassword")}
            <input
              type="password"
              autoComplete="new-password"
              value={form.confirm}
              required
              minLength={8}
              onChange={(e) => setForm({ ...form, confirm: e.target.value })}
            />
          </label>
          <div>
            <button type="submit" className="kn-btn-wide kn-btn-primary" disabled={change.isPending}>
              {t("account.changePassword")}
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}
