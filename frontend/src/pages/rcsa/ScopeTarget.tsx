import { useTranslation } from "react-i18next";
import type { ImplementationSnapshot } from "./api";

export function ScopeTarget({ snapshot, details = false }: { snapshot?: ImplementationSnapshot | null; details?: boolean }) {
  const { t } = useTranslation();
  if (!snapshot) return null;
  return <div className="kn-secondary">
    {snapshot.asset ? `${snapshot.asset.name} · ${snapshot.asset.environment}` : t("rcsa.scope.process")}
    {details && <p>{snapshot.description}</p>}
  </div>;
}
