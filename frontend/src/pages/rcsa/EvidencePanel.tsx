// 证据引用：已登记的证据或外部位置。只引用，不上传；链接只供人点开，服务端不抓取。
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { request } from "../../api";
import { Card, EmptyState } from "../../components/ui";
import { send, type Response } from "./api";
import { ErrorNotice, PersonSelect, usePeople } from "./shared";

interface EvidenceRecord {
  id: number;
  control_id: number;
  title: string;
  display_status: string;
  valid_until: string | null;
}

export function EvidencePanel({
  response, controlId, editable, onChanged,
}: { response: Response; controlId: number; editable: boolean; onChanged: () => void }) {
  const { t } = useTranslation();
  const { people, nameOf } = usePeople();
  const records = useQuery({
    queryKey: ["evidence", "for-rcsa"],
    queryFn: () => request<EvidenceRecord[]>("/api/evidence?limit=500"),
    enabled: editable,
  });
  const [registered, setRegistered] = useState("");
  const [location, setLocation] = useState("");
  const [purpose, setPurpose] = useState<"self_assessment" | "independent_test">("self_assessment");
  const [tester, setTester] = useState<number | null>(null);
  const [testerName, setTesterName] = useState("");
  const [testedAt, setTestedAt] = useState("");
  const [scope, setScope] = useState("");
  const [conclusion, setConclusion] = useState("");
  const [basis, setBasis] = useState("");
  const [note, setNote] = useState("");
  const attach = useMutation({
    mutationFn: () => send(`/api/rcsa/responses/${response.id}/evidence`, "POST", {
      ...(registered ? { evidence_item_id: Number(registered) } : {}),
      ...(location.trim() ? { external_location: location.trim() } : {}),
      purpose, coverage_note: note,
      ...(purpose === "independent_test" ? {
        ...(tester ? { tester_user_id: tester } : { tester_name: testerName }),
        tested_at: testedAt || null, test_scope: scope, conclusion, independence_basis: basis,
      } : {}),
    }),
    onSuccess: () => { setRegistered(""); setLocation(""); setNote(""); onChanged(); },
  });
  const detach = useMutation({
    mutationFn: (id: number) => send(`/api/rcsa/responses/${response.id}/evidence/${id}`, "DELETE", {}),
    onSuccess: onChanged,
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    attach.mutate();
  };
  const forControl = (records.data ?? []).filter((record) => record.control_id === controlId);

  return (
    <Card title={t("rcsa.evidence.title")}>
      {response.evidence.length === 0 ? <EmptyState compact title={t("rcsa.evidence.none")} /> : (
        <ul className="kn-stack-2">
          {response.evidence.map((row) => (
            <li key={row.id}>
              <strong>{row.snapshot.title ?? row.external_location}</strong>{" "}
              <span className="kn-secondary">· {t(`rcsa.evidence.purposes.${row.purpose}`)}</span>
              {row.snapshot.valid_until ? <span className="kn-secondary"> · {t("rcsa.evidence.validUntil", { date: row.snapshot.valid_until })}</span> : null}
              {row.purpose === "independent_test" ? (
                <div className="kn-secondary kn-text-xs">
                  {t("rcsa.evidence.testedBy", { name: row.tester_user_id ? nameOf(row.tester_user_id) : row.tester_name, date: row.tested_at })}
                  {" — "}{row.conclusion}
                </div>
              ) : null}
              {editable && (
                <button type="button" className="kn-btn-sm" disabled={detach.isPending} onClick={() => detach.mutate(row.id)}>
                  {t("rcsa.evidence.remove")}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {editable && (
        <form className="rcsa-form kn-mt-1" onSubmit={submit} aria-label={t("rcsa.evidence.add")}>
          <label>{t("rcsa.evidence.registered")}
            <select value={registered} onChange={(e) => setRegistered(e.target.value)}>
              <option value="">{t("rcsa.evidence.noneChosen")}</option>
              {forControl.map((record) => <option key={record.id} value={record.id}>{record.title}</option>)}
            </select>
          </label>
          <label>{t("rcsa.evidence.location")}
            <input value={location} onChange={(e) => setLocation(e.target.value)} placeholder={t("rcsa.evidence.locationHint")} />
          </label>
          <label>{t("rcsa.evidence.purpose")}
            <select value={purpose} onChange={(e) => setPurpose(e.target.value as typeof purpose)}>
              <option value="self_assessment">{t("rcsa.evidence.purposes.self_assessment")}</option>
              <option value="independent_test">{t("rcsa.evidence.purposes.independent_test")}</option>
            </select>
          </label>
          <label>{t("rcsa.evidence.coverage")}
            <input value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          {purpose === "independent_test" && (
            <>
              <PersonSelect label={t("rcsa.evidence.tester")} value={tester} onChange={setTester} people={people} />
              {!tester && (
                <label>{t("rcsa.evidence.externalTester")}
                  <input value={testerName} onChange={(e) => setTesterName(e.target.value)} />
                </label>
              )}
              <label>{t("rcsa.evidence.testedAt")}<input type="date" value={testedAt} onChange={(e) => setTestedAt(e.target.value)} /></label>
              <label>{t("rcsa.evidence.testScope")}<input value={scope} onChange={(e) => setScope(e.target.value)} /></label>
              <label>{t("rcsa.evidence.conclusion")}<input value={conclusion} onChange={(e) => setConclusion(e.target.value)} /></label>
              <label>{t("rcsa.evidence.independence")}<input value={basis} onChange={(e) => setBasis(e.target.value)} /></label>
            </>
          )}
          <div className="rcsa-actions rcsa-wide">
            <button type="submit" className="kn-btn kn-btn-secondary" disabled={(!registered && !location.trim()) || attach.isPending}>
              {t("rcsa.evidence.add")}
            </button>
          </div>
          <div className="rcsa-wide"><ErrorNotice error={attach.error ?? detach.error} /></div>
        </form>
      )}
    </Card>
  );
}
