import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

/** 一个表单依赖的前置数据：为空时这个表单就填不完。 */
export interface Prerequisite {
  /** 这一项是否已经满足（通常是 `list.length > 0`） */
  satisfied: boolean;
  /** 缺什么，给人看的名字 */
  label: string;
  /** 去哪儿补 */
  to: string;
}

/**
 * 冷启动引导：当表单的必填下拉没有任何可选项时，说清楚缺什么、去哪儿补。
 *
 * 为什么要有这个组件而不是在各页面里写一段：新部署时控制点库、证据类型、
 * 合规框架全是空的，凡是依赖它们的表单都会出现"保存按钮永久置灰且不解释"
 * 的死锁。只修撞见的那一个是打地鼠——这类页面至少有证据登记和成熟度评估两处。
 *
 * **只列没满足的那几项。** 把满足的也列出来，人补完一项发现提示没变，
 * 会以为自己补错了地方。
 */
export function PrerequisiteNotice({ items }: { items: Prerequisite[] }) {
  const { t } = useTranslation();
  const missing = items.filter((item) => !item.satisfied);
  if (missing.length === 0) return null;

  return (
    <p role="note" className="kn-prereq-notice">
      {t("common.prerequisiteMissing")}{" "}
      {missing.map((item, index) => (
        <span key={item.to}>
          {index > 0 && "、"}
          <Link to={item.to}>{item.label}</Link>
        </span>
      ))}
    </p>
  );
}
