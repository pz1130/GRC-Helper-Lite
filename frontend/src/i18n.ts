import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import zh from "./locales/zh.json";

/**
 * 查不到的键**绝不原样吐给用户**。
 *
 * 全站有 50+ 处 `t(`prefix.${value}`)` 这样的拼接，value 多半来自接口。value
 * 一旦是 undefined 或一个没预料到的枚举值，i18next 默认行为是把整条键当文案
 * 显示——界面上就出现 `evidence.statuses.undefined`、
 * `techAssets.environments.undefined` 这类内部标识（2026-09-20 上线前测试报告
 * 报了 3 处，实际风险点有 13 处）。
 *
 * 逐处加 defaultValue 是打地鼠：漏一处就再漏一次，新写的代码还会再犯。这里一次
 * 兜住：`xxx.undefined` 显示成占位符，其余缺失的键退回**最后一段**（看到
 * "collected" 比看到 "evidence.statuses.collected" 强，也不泄漏内部路径）。
 *
 * 这不是拿来盖住缺词条的——后端 code 对应的词条由 `make i18n-check` 把关。
 */
function parseMissingKeyHandler(key: string): string {
  const last = key.split(".").pop() ?? "";
  return !last || last === "undefined" || last === "null" ? "—" : last;
}

// 界面支持英文与简体中文，默认英文；用户的选择存在 localStorage，并同步到 <html lang>。
// 后端只给 code，句子按 code 在语言包里查——这套机制与语言数量无关。
export type Lang = "en" | "zh";
const LANG_KEY = "grc.lang";

function initialLang(): Lang {
  const saved = typeof localStorage !== "undefined" ? localStorage.getItem(LANG_KEY) : null;
  return saved === "zh" ? "zh" : "en";
}

function syncHtmlLang(lng: string): void {
  if (typeof document !== "undefined") document.documentElement.lang = lng === "zh" ? "zh-CN" : "en";
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, zh: { translation: zh } },
  lng: initialLang(),
  fallbackLng: "en",
  supportedLngs: ["en", "zh"],
  interpolation: { escapeValue: false },
  parseMissingKeyHandler,
});
syncHtmlLang(i18n.language);
i18n.on("languageChanged", syncHtmlLang);

export function setLanguage(lng: Lang): void {
  localStorage.setItem(LANG_KEY, lng);
  void i18n.changeLanguage(lng);
}

export default i18n;

/**
 * 这个键查得到，**而且是一句话**。凡是「有词条就翻译、没有就回落」的地方都用它。
 *
 * 不能只用 `i18n.exists`：业务错误里有 HTTP 类别码（`conflict`、`not_found`），
 * 也有以同名开头的具体码（`conflict.payload_invalid`）。后者在词条里是嵌套的，
 * 于是 `errors.conflict` 是一组词条而不是一句话，`exists` 照样返回 true，
 * `t()` 给出的是 "returned an object instead of string"，直接显示到界面上。
 */
export function hasText(key: string): boolean {
  return i18n.exists(key) && typeof i18n.t(key, { returnObjects: true }) === "string";
}

/** 后端把多项传成列表（顿号是中文排版，不能拼进参数），这里按当前语言连接。 */
export function joinList(value: unknown): string {
  if (!Array.isArray(value)) return String(value ?? "");
  return value.map(String).join(i18n.t("common.listSeparator"));
}

/** 把 params 里的数组按当前语言连接好，再交给 i18next 插值。 */
export function renderParams(params?: Record<string, unknown>): Record<string, unknown> {
  if (!params) return {};
  return Object.fromEntries(
    Object.entries(params).map(([k, v]) => [k, Array.isArray(v) ? joinList(v) : v]),
  );
}

/**
 * 导入校验报告里的一条问题（Excel 控制矩阵、框架模板）。后端只给 code 与参数，
 * 句子在这里按当前语言组；`detail` 是后端的中文兜底，词条缺失时才显示。
 */
export interface Issue {
  code: string;
  detail: string;
  params: Record<string, unknown>;
}

export function issueText(issue: Issue): string {
  const key = `errors.${issue.code}`;
  return hasText(key) ? i18n.t(key, renderParams(issue.params)) : issue.detail;
}
