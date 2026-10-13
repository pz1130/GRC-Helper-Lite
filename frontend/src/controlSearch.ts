import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { request } from "./api";

export interface ControlOption {
  id: number;
  code: string;
  title: string;
}

/** 一次只取这么多条命中：下拉是用来挑的，不是用来翻的。命中满了就提示再多打几个字。 */
export const CONTROL_SEARCH_LIMIT = 50;

/**
 * 按代码 / 标题 / 陈述搜控制点，给表单下拉用。
 *
 * 原来证据登记和关系图各自一次取 `limit=500`（后端上限），第 501 个以后的控制点
 * 根本选不到（KNOWN_ISSUES K11）。现在只取命中的前几十条，由人输入去缩小范围。
 *
 * 输入停 250ms 再发请求；换关键词时保留上一批结果，下拉不会闪成空的。
 */
export function useControlSearch(query: string, enabled = true) {
  const [debounced, setDebounced] = useState(query.trim());
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);
  return useQuery({
    queryKey: ["controls", "search", debounced],
    queryFn: () =>
      request<ControlOption[]>(
        `/api/controls?limit=${CONTROL_SEARCH_LIMIT}&q=${encodeURIComponent(debounced)}`,
      ),
    enabled,
    placeholderData: keepPreviousData,
  });
}

/**
 * 已选中的那一项必须一直在选项里：不在的话受控 `<select>` 会显示成第一项
 * （「请选择」），人以为没选，其实表单里有值。编辑一条证据时正是这样。
 */
export function withSelected(options: ControlOption[] | undefined, selected: ControlOption | null): ControlOption[] {
  const list = options ?? [];
  if (!selected || list.some((option) => option.id === selected.id)) return list;
  return [selected, ...list];
}
