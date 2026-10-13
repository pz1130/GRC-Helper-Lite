import i18n, { hasText, renderParams } from "./i18n";
import { DEMO_TOKEN, getMockResponse } from "./mockData";

const TOKEN_KEY = "grc.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  /**
   * 后端的 `message` 是写死的中文句子，直接显示等于英文界面上冒中文。
   * 所以在**构造这一处**按 `code` 查词条，全站几十处 `{error.message}`
   * 一个都不用改。
   *
   * 迁移是增量的：还没给到具体 code 的错误（`code` 仍是 `not_found` /
   * `conflict` 这类 HTTP 类别）照旧显示后端那句中文。
   *
   * **这里必须用 `hasText`（i18n.ts）而不是 `defaultValue`**：`i18n.ts` 的
   * `parseMissingKeyHandler` 会把查不到的键回退成**最后一段**，它的优先级
   * 高于 `defaultValue`，于是 `errors.conflict` 会显示成 "conflict"——
   * 界面上冒出一个内部标识，比原来的中文更糟。那个 handler 是给
   * `t(`prefix.${enumValue}`)` 那类拼接键用的，不适用于这里。
   *
   * 语言在构造时定下：错误是一闪而过的东西，不值得为它订阅语言变化。
   */
  constructor(
    public status: number,
    public code: string,
    message: string,
    params?: Record<string, unknown>,
  ) {
    const key = `errors.${code}`;
    super(hasText(key) ? i18n.t(key, renderParams(params)) : message);
  }
}

/** 演示会话里，一个 mock 没覆盖的接口。 */
export function demoNotCovered(): ApiError {
  return new ApiError(501, "demo_not_covered", i18n.t("common.demoNotCovered"));
}

/** 当前是不是离线演示会话。 */
export function isDemoSession(): boolean {
  return getToken() === DEMO_TOKEN;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();

  // 演示模式：直接返回形状与真实接口一致的 mock 数据，不发请求。
  if (token === DEMO_TOKEN) {
    const mock = getMockResponse(path, init);
    if (mock !== undefined) {
      return mock as T;
    }
    // **演示模式绝不穿透到真后端。** 穿透会带着 `demo-mock-token` 打过去，
    // 后端必然 401，而下面 401 的处理是 setToken(null)——人就被踢回登录页了
    // （2026-09-20 测试报告里"测试中 2 次被踢回登录页"就是这么来的）。
    throw demoNotCovered();
  }

  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  });

  if (response.status === 401) {
    setToken(null);
    throw new ApiError(401, "unauthorized", i18n.t("common.unauthorized"));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.code ?? "error", body.message ?? i18n.t("common.requestFailed"), body.params);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export async function download(path: string): Promise<Blob> {
  const token = getToken();
  if (token === DEMO_TOKEN) {
    return new Blob(["Demo document content"], { type: "text/plain" });
  }

  const response = await fetch(path, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (response.status === 401) {
    setToken(null);
    throw new ApiError(401, "unauthorized", i18n.t("common.unauthorized"));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.code ?? "error", body.message ?? i18n.t("common.requestFailed"), body.params);
  }
  return response.blob();
}
