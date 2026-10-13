import i18n, { hasText, renderParams } from "./i18n";

export type ParseWarningCode = { code: string; params?: Record<string, unknown> };

/**
 * 解析告警的显示文案。
 *
 * 后端并行给两份：`parse_warnings` 是写死的中文句子，`parse_warning_codes`
 * 是同一批告警的 code 与变量。有 code 就按 code 翻译，没有就用那句中文——
 * 迁移（`0026`）之前存进库的行 `parse_warning_codes` 是 NULL，显示效果与
 * 迁移前完全一致。
 *
 * 和 `ApiError` 一样用 `hasText` 而不是 `defaultValue`：`i18n.ts` 的
 * `parseMissingKeyHandler` 会把查不到的键回退成最后一段，优先级高于
 * defaultValue，界面上会冒出 `one_clause_dominates` 这样的内部标识。
 */
export function parseWarningText(
  sentences: string | null | undefined,
  codes: ParseWarningCode[] | null | undefined,
): string | null {
  if (codes && codes.length > 0) {
    const lines = codes.map((entry) => {
      const key = `parseWarnings.${entry.code}`;
      return hasText(key) ? i18n.t(key, renderParams(entry.params)) : null;
    });
    // 只要有一条翻不了，整段退回后端那份——中英混排比全中文更难读。
    if (lines.every((line) => line !== null)) return lines.join("\n");
  }
  return sentences ?? null;
}

/**
 * 解析失败原因的显示文案。与告警同一个道理：`parse_error` 是后端的中文句子，
 * `parse_error_code` 是它的 code（0027 之前的老行为 NULL，退回那句原文）。
 */
export function parseErrorText(doc: {
  parse_error: string | null;
  parse_error_code?: string | null;
  parse_error_params?: Record<string, unknown> | null;
}): string | null {
  if (!doc.parse_error && !doc.parse_error_code) return null;
  const key = doc.parse_error_code ? `errors.${doc.parse_error_code}` : null;
  if (key && hasText(key)) return i18n.t(key, renderParams(doc.parse_error_params ?? undefined));
  return doc.parse_error;
}
