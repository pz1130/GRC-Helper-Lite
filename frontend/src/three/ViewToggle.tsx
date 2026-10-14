import { useState } from "react";
import { useTranslation } from "react-i18next";

import { hasWebGL, readViewMode, writeViewMode } from "./support";

/** 2D / 3D 切换的状态与按钮。没有 WebGL 时 3D 按钮禁用并说明原因。 */
export function useViewMode(storageKey: string) {
  const [mode, setMode] = useState<"2d" | "3d">(() => readViewMode(storageKey));
  const update = (next: "2d" | "3d") => {
    setMode(next);
    writeViewMode(storageKey, next);
  };
  return [mode, update] as const;
}

export function ViewToggle({ mode, onChange }: { mode: "2d" | "3d"; onChange: (mode: "2d" | "3d") => void }) {
  const { t } = useTranslation();
  const webgl = hasWebGL();
  return (
    <div className="kn-segmented" role="group" aria-label={t("view3d.label")}>
      {(["2d", "3d"] as const).map((value) => (
        <button
          key={value}
          type="button"
          className={`kn-segmented-item ${mode === value ? "active" : ""}`}
          aria-pressed={mode === value}
          disabled={value === "3d" && !webgl}
          title={value === "3d" && !webgl ? t("view3d.unavailable") : undefined}
          onClick={() => onChange(value)}
        >
          {t(`view3d.${value}`)}
        </button>
      ))}
    </div>
  );
}
