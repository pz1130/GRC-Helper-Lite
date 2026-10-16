import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";

/**
 * 页面级兜底。包在 `<Outlet />` 外面，不是包在整棵路由外面——
 * 导航栏和侧边栏要活着，人才能走去别的页面，而不是只剩一个空白屏和刷新键。
 *
 * 为什么需要它：React 的默认行为是任一子组件渲染期抛错就**卸载整棵树**，
 * `#root` 变成空字符串。2026-09-20 的上线前测试在演示模式下撞到 6 个这样的
 * 白屏，每一个单独看都只是"某个字段是 undefined"的小问题，叠加"全站无兜底"
 * 之后才变成整页塌掉。兜底不治根因，但把"整站白屏"降成"这一块没出来"。
 *
 * `resetKey` 用当前路径：换一页就清掉错误状态。没有它，人撞一次错就被钉死在
 * 错误页上，点侧边栏也出不去——那比白屏还难受。
 */

interface Props {
  children: ReactNode;
  resetKey: string;
  fallback: (error: Error, retry: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

class Boundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 留在控制台：白屏最难查的一点就是堆栈被一起吞掉了。
    console.error("页面渲染失败", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error) {
      return this.props.fallback(this.state.error, () => this.setState({ error: null }));
    }
    return this.props.children;
  }
}

export function PageErrorBoundary({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const location = useLocation();
  return (
    <Boundary
      resetKey={location.pathname}
      fallback={(error, retry) => (
        <section className="kn-card" role="alert" style={{ padding: 24 }}>
          <h2 className="kn-mt-0">{t("common.pageFailed")}</h2>
          <p className="kn-secondary">{t("common.pageFailedHint")}</p>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              fontSize: "0.8125rem",
              color: "var(--text-secondary)",
              background: "var(--surface-sunken, rgba(0,0,0,0.04))",
              padding: 12,
              borderRadius: 6,
            }}
          >
            {error.message}
          </pre>
          <button className="kn-btn-primary" onClick={retry}>
            {t("common.retry")}
          </button>
        </section>
      )}
    >
      {children}
    </Boundary>
  );
}
