import { useState } from "react";
import { Link } from "react-router-dom";
import { useTfsa, useTfsaConfig, useTfsaDelete, useTfsaRecord } from "../api/hooks";
import type { TfsaRecordResult, TfsaSummary, TfsaTxn, TfsaTxnType, TfsaWarning } from "../api/types";
import { fmtMoney } from "../lib/format";

const INPUT =
  "rounded-md border border-border bg-panel px-2 py-1 text-[13px] focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20";
const TYPE_LABEL: Record<TfsaTxnType, string> = {
  contribution: "Contribution",
  withdrawal: "Withdrawal",
  transfer: "Transfer (TFSA → TFSA)",
};
const KNOWN_INSTITUTIONS = ["Wealthsimple", "Questrade", "TD"];

const cad = (v: number | null | undefined) => fmtMoney(v, "CAD");

function Alert({ level, children }: { level: TfsaWarning["level"]; children: React.ReactNode }) {
  const cls =
    level === "danger"
      ? "border-down/40 bg-down/10 text-down"
      : level === "warning"
        ? "border-accent/30 bg-accent-soft text-text"
        : "border-border bg-bg text-muted";
  return <div className={`rounded-md border px-2.5 py-1.5 text-[12px] ${cls}`}>{children}</div>;
}

function Row({ label, value, sub }: { label: string; value: string; sub?: React.ReactNode }) {
  return (
    <div className="py-0.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-muted">{label}</span>
        <span className="num">{value}</span>
      </div>
      {sub && <div className="text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

function RecordForm({ t }: { t: TfsaSummary }) {
  const record = useTfsaRecord();
  const [form, setForm] = useState({ date: t.today, institution: "Wealthsimple", type: "contribution" as TfsaTxnType, amount: "", note: "" });
  const [pending, setPending] = useState<TfsaRecordResult | null>(null); // preview awaiting "Record anyway"
  const [done, setDone] = useState<TfsaRecordResult | null>(null);
  const institutions = [...new Set([...KNOWN_INSTITUTIONS, ...t.transactions.map((x) => x.institution)])];
  const valid = Boolean(form.date) && form.institution.trim() !== "" && Number(form.amount) > 0;

  const set = (patch: Partial<typeof form>) => {
    setForm((f) => ({ ...f, ...patch }));
    setPending(null);
    setDone(null);
    record.reset();
  };

  const submit = async () => {
    const txn = { ...form, amount: form.amount.trim(), note: form.note.trim() || undefined };
    try {
      if (!pending) {
        const preview = await record.mutateAsync({ ...txn, commit: false });
        if (preview.warnings.some((w) => w.level !== "info")) {
          setPending(preview);
          return;
        }
      }
      setDone(await record.mutateAsync({ ...txn, commit: true }));
      setPending(null);
      setForm((f) => ({ ...f, amount: "", note: "" }));
    } catch {
      /* shown via record.error */
    }
  };

  return (
    <div className="space-y-2 border-t border-border pt-2">
      <div className="grid grid-cols-2 gap-1.5">
        <input type="date" value={form.date} onChange={(e) => set({ date: e.target.value })} className={`${INPUT} num`} />
        <input
          type="number"
          inputMode="decimal"
          min="0"
          step="0.01"
          placeholder="Amount (CAD)"
          value={form.amount}
          onChange={(e) => set({ amount: e.target.value })}
          className={`${INPUT} num`}
        />
        <select value={form.type} onChange={(e) => set({ type: e.target.value as TfsaTxnType })} className={INPUT}>
          {(Object.keys(TYPE_LABEL) as TfsaTxnType[]).map((k) => (
            <option key={k} value={k}>
              {TYPE_LABEL[k]}
            </option>
          ))}
        </select>
        <input
          list="tfsa-institutions"
          placeholder="Institution"
          value={form.institution}
          onChange={(e) => set({ institution: e.target.value })}
          className={INPUT}
        />
        <datalist id="tfsa-institutions">
          {institutions.map((i) => (
            <option key={i} value={i} />
          ))}
        </datalist>
        <input
          placeholder="Note (optional)"
          value={form.note}
          onChange={(e) => set({ note: e.target.value })}
          className={`${INPUT} col-span-2`}
        />
      </div>
      {pending?.warnings.map((w) => (
        <Alert key={w.code} level={w.level}>
          {w.message}
        </Alert>
      ))}
      {record.isError && <Alert level="danger">{(record.error as Error).message}</Alert>}
      {done && (
        <div className="space-y-1">
          <p className="text-[12px] text-up">
            Recorded.{done.room_after != null && ` ${cad(done.room_after)} of ${done.year} room left.`}
          </p>
          {done.warnings.map((w) => (
            <Alert key={w.code} level={w.level}>
              {w.message}
            </Alert>
          ))}
        </div>
      )}
      <div className="flex gap-1.5">
        <button
          onClick={submit}
          disabled={!valid || record.isPending}
          className={`rounded-md px-3 py-1 text-[13px] font-medium text-white disabled:opacity-50 ${
            pending?.warnings.some((w) => w.level === "danger") ? "bg-down hover:opacity-90" : "bg-accent hover:bg-accent-deep"
          }`}
        >
          {record.isPending ? "Checking…" : pending ? "Record anyway" : "Record"}
        </button>
        {pending && (
          <button onClick={() => setPending(null)} className="rounded-md border border-border bg-panel px-3 py-1 text-[13px] hover:bg-bg">
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

function History({ rows }: { rows: TfsaTxn[] }) {
  const del = useTfsaDelete();
  if (!rows.length) return <p className="border-t border-border pt-2 text-[12px] text-muted">No transactions recorded yet.</p>;
  return (
    <table className="w-full border-t border-border text-[12px]">
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className="border-b border-border/50 align-top">
            <td className="num py-1 pr-2 text-muted">{r.date}</td>
            <td className="py-1 pr-2">
              {r.institution}
              <div className="text-[11px] text-muted">
                {r.type === "transfer" ? "Transfer · no room effect" : TYPE_LABEL[r.type]}
                {r.note ? ` · ${r.note}` : ""}
              </div>
            </td>
            <td className={`num py-1 text-right ${r.type === "transfer" ? "text-muted" : ""}`}>{cad(r.amount)}</td>
            <td className="py-1 pl-2 text-right">
              <button
                onClick={() => confirm(`Delete the ${r.date} ${r.type} of ${cad(r.amount)}?`) && del.mutate(r.id)}
                title="Delete"
                className="text-muted hover:text-down"
              >
                ×
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Reconcile({ t }: { t: TfsaSummary }) {
  const save = useTfsaConfig();
  const r = t.reconcile!;
  const [value, setValue] = useState(String(r.computed_jan1));
  return (
    <Alert level="warning">
      <p>
        March check: compare the Jan 1, {r.year} TFSA room in CRA My Account with the app's {cad(r.computed_jan1)}. Confirm
        it (or enter CRA's figure) to re-anchor on {r.year}.
      </p>
      <div className="mt-1.5 flex gap-1.5">
        <input value={value} onChange={(e) => setValue(e.target.value)} className={`${INPUT} num w-32`} />
        <button
          onClick={() => save.mutate({ limits: t.limits, anchor: { year: r.year, room: value.trim() } })}
          disabled={save.isPending || !value.trim()}
          className="rounded-md bg-accent px-3 py-1 text-[12px] font-medium text-white hover:bg-accent-deep disabled:opacity-50"
        >
          Confirm CRA figure
        </button>
      </div>
      {save.isError && <p className="mt-1 text-down">{(save.error as Error).message}</p>}
    </Alert>
  );
}

export function TfsaRoom() {
  const tfsa = useTfsa();
  const [panel, setPanel] = useState<"none" | "record" | "history">("none");
  const t = tfsa.data;
  const toggle = (p: typeof panel) => setPanel((cur) => (cur === p ? "none" : p));
  const tab = (p: typeof panel, label: string) => (
    <button
      onClick={() => toggle(p)}
      className={`px-2 py-0.5 text-[12px] ${panel === p ? "bg-accent font-medium text-white" : "text-muted hover:text-text"}`}
    >
      {label}
    </button>
  );

  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 className="text-[13px] font-medium">TFSA room{t?.year ? ` · ${t.year}` : ""}</h2>
        {t?.configured && (
          <div className="flex overflow-hidden rounded-md border border-border">
            {tab("record", "Record")}
            {tab("history", `History (${t.transactions.length})`)}
          </div>
        )}
      </div>
      <div className="space-y-2 p-3 text-[13px]">
        {tfsa.isLoading ? (
          <p className="text-muted">Loading…</p>
        ) : tfsa.isError ? (
          <p className="text-down">{(tfsa.error as Error).message}</p>
        ) : !t?.configured ? (
          <p className="text-muted">
            Enter CRA's Jan 1 contribution room in{" "}
            <Link to="/settings" className="text-accent underline">
              Settings
            </Link>{" "}
            to start tracking.
          </p>
        ) : (
          <>
            <div>
              <div className={`num text-[22px] font-semibold ${t.remaining! < 0 ? "text-down" : ""}`}>{cad(t.remaining)}</div>
              <div className="text-[11px] text-muted">left to contribute in {t.year} · all institutions</div>
            </div>
            {t.overcontribution && (
              <Alert level="danger">
                <span className="font-medium">Over-contributed by {cad(t.overcontribution.excess)}</span>
                {t.overcontribution.since && ` since ${t.overcontribution.since}`}. CRA taxes the excess 1% a month (≈{" "}
                {cad(t.overcontribution.monthly_tax)}/month) until it's withdrawn.
              </Alert>
            )}
            {t.reconcile && <Reconcile key={t.reconcile.year} t={t} />}
            <div>
              <Row label={`Jan 1, ${t.year} room`} value={cad(t.jan1)} />
              <Row label="Contributed" value={cad(t.contributed)} />
              <Row
                label="Withdrawn"
                value={cad(t.withdrawn)}
                sub={t.withdrawn ? `returns as room on Jan 1, ${t.next_year} — not this year` : undefined}
              />
              <Row
                label={`Jan 1, ${t.next_year} projected`}
                value={`${t.next_limit_provisional ? "≈ " : ""}${cad(t.projected_next_jan1)}`}
                sub={
                  t.next_limit_provisional ? (
                    <>
                      {t.next_year} limit not announced yet (usually November) — assumes {cad(t.next_limit)}.{" "}
                      <Link to="/settings" className="text-accent underline">
                        Set it
                      </Link>
                    </>
                  ) : (
                    `unused room + ${cad(t.next_limit)} limit + this year's withdrawals`
                  )
                }
              />
            </div>
            {panel === "record" && <RecordForm t={t} />}
            {panel === "history" && <History rows={t.transactions} />}
          </>
        )}
      </div>
    </section>
  );
}
