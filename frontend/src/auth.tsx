import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { getToken, request, setToken } from "./api";
import { DEMO_TOKEN, DEMO_USER } from "./mockData";

export type Role = "admin" | "grc_lead" | "contributor" | "viewer";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
}

interface AuthValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  enterDemo: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    if (token === DEMO_TOKEN) {
      setUser(DEMO_USER);
      setLoading(false);
      return;
    }
    request<User>("/api/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const result = await request<{ access_token: string; user: User }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setToken(result.access_token);
    setUser(result.user);
  }, []);

  const enterDemo = useCallback(() => {
    setToken(DEMO_TOKEN);
    setUser(DEMO_USER);
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, enterDemo }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within an AuthProvider");
  return value;
}

/** 前端的权限判断只用于隐藏入口；真正的拦截在后端（spec §8.1）。 */
export function canManageLlmConfig(role: Role | undefined): boolean {
  return role === "admin";
}

export function canReadAuditLog(role: Role | undefined): boolean {
  return role === "admin" || role === "grc_lead";
}

/** 证据类型是参照数据，谁能登记证据谁就能维护它（后端是 EVIDENCE_WRITE）。 */
export function canWriteEvidence(role: Role | undefined): boolean {
  return role === "admin" || role === "grc_lead" || role === "contributor";
}
