import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { request } from "../api";
import { useTranslation } from "react-i18next";

import type { NavGroup } from "./nav";

/**
 * 命令面板：只做「跳转到某页」，不调任何接口。
 * 全文检索仍由 /search 页负责——面板里最后一项就是去那里，
 * 这样 ⌘K 的老习惯（直达搜索）只多一次回车。
 */
export function CommandPalette({ open, onClose, groups }: { open: boolean; onClose: () => void; groups: NavGroup[] }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // 控制点与风险用页面本来就在调的列表接口，只在面板打开时取、缓存 60 秒；
  // 过滤在客户端做——条目量是一个机构的控制目录，几百条以内。
  const controls = useQuery({
    queryKey: ["palette", "controls"],
    queryFn: () => request<{ id: number; code: string; title: string }[]>("/api/controls"),
    enabled: open,
    staleTime: 60_000,
  });
  const risks = useQuery({
    queryKey: ["palette", "risks"],
    queryFn: () => request<{ id: number; title: string; inherent_score: number }[]>("/api/risks"),
    enabled: open,
    staleTime: 60_000,
  });

  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pages = groups.flatMap((group) => group.items.map((item) => ({ key: item.to, to: item.to, icon: item.icon, label: item.label, group: group.title })));
    if (!q) return pages;
    const hits = pages.filter((item) => item.label.toLowerCase().includes(q) || item.group.toLowerCase().includes(q));
    const controlHits = (controls.data ?? [])
      .filter((c) => c.code.toLowerCase().includes(q) || c.title.toLowerCase().includes(q))
      .slice(0, 6)
      .map((c) => ({ key: `control-${c.id}`, to: `/controls/${c.id}`, icon: <code>{c.code}</code>, label: c.title, group: t("topbar.paletteControls") }));
    const riskHits = (risks.data ?? [])
      .filter((r) => r.title.toLowerCase().includes(q))
      .slice(0, 6)
      .map((r) => ({ key: `risk-${r.id}`, to: "/risks", icon: <span className="ws-palette-score">{r.inherent_score}</span>, label: r.title, group: t("topbar.paletteRisks") }));
    return [...hits, ...controlHits, ...riskHits];
  }, [groups, query, controls.data, risks.data, t]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => setCursor(0), [query]);

  if (!open) return null;

  const go = (to: string) => {
    onClose();
    navigate(to);
  };
  // 末尾固定一项「去全文搜索」，所以可选总数比 items 多一。
  const total = items.length + 1;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") onClose();
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => (c + 1) % total);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => (c - 1 + total) % total);
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(cursor < items.length ? items[cursor].to : "/search");
    }
  };

  return (
    <div className="ws-palette-backdrop" onMouseDown={onClose}>
      <div
        className="ws-palette"
        role="dialog"
        aria-modal="true"
        aria-label={t("topbar.quickSearch")}
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="ws-palette-input">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("topbar.searchPlaceholder").split(" (")[0]}
            aria-label={t("topbar.quickSearch")}
            aria-activedescendant={`ws-cmd-${cursor}`}
          />
          <kbd className="kn-cmd-kbd">Esc</kbd>
        </div>
        <ul className="ws-palette-list" role="listbox">
          {items.map((item, index) => (
            <li
              key={item.key}
              id={`ws-cmd-${index}`}
              role="option"
              aria-selected={cursor === index}
              className={`ws-palette-item${cursor === index ? " active" : ""}`}
              onMouseEnter={() => setCursor(index)}
              onClick={() => go(item.to)}
            >
              <span className="ws-palette-icon" aria-hidden="true">{item.icon}</span>
              <span className="ws-palette-label">{item.label}</span>
              <span className="ws-palette-group">{item.group}</span>
            </li>
          ))}
          <li
            id={`ws-cmd-${items.length}`}
            role="option"
            aria-selected={cursor === items.length}
            className={`ws-palette-item ws-palette-item--search${cursor === items.length ? " active" : ""}`}
            onMouseEnter={() => setCursor(items.length)}
            onClick={() => go("/search")}
          >
            <span className="ws-palette-icon" aria-hidden="true">↵</span>
            <span className="ws-palette-label">{t("nav.search")}</span>
          </li>
        </ul>
      </div>
    </div>
  );
}
