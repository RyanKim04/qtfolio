import { useHealth, useSummary } from "../api/hooks";
import { useCurrency } from "../App";
import type { Currency } from "../api/types";
import { fmtMoney, fmtPct, fmtSignedMoney, pnlClass } from "../lib/format";

function syncDot(status: "ok" | "warn" | "err"): string {
  if (status === "ok") return "bg-up";
  if (status === "warn") return "bg-accent";
  return "bg-down";
}

export function Header() {
  const { currency, setCurrency } = useCurrency();
  const summary = useSummary(currency);
  const health = useHealth();

  const token = health.data?.token.status;
  const jobsOk = health.data?.jobs.every((j) => !j.last_run || j.last_run.status !== "error") ?? true;
  const dot = token === "broken" ? "err" : token === "ok" && jobsOk ? "ok" : "warn";

  const s = summary.data;
  return (
    <header className="flex items-center gap-6 border-b border-border bg-panel px-4 py-2">
      <div className="flex items-baseline gap-3">
        <span className="num text-xl font-semibold tracking-tight">
          {s && !s.empty ? fmtMoney(s.total_value, currency) : "—"}
        </span>
        {s && !s.empty && (
          <span className={`num text-[13px] ${pnlClass(s.day_pnl)}`}>
            {fmtSignedMoney(s.day_pnl, currency)} ({fmtPct(s.day_pnl_pct)})
          </span>
        )}
      </div>
      <div className="ml-auto flex items-center gap-4">
        <div className="flex overflow-hidden rounded-md border border-border">
          {(["CAD", "USD"] as Currency[]).map((c) => (
            <button
              key={c}
              onClick={() => setCurrency(c)}
              className={`num px-2.5 py-0.5 text-[12px] font-medium ${
                currency === c ? "bg-accent text-white" : "bg-panel text-muted hover:text-text"
              }`}
            >
              {c}
            </button>
          ))}
        </div>
        <span
          className={`h-2 w-2 rounded-full ${syncDot(dot)}`}
          title={`token: ${token ?? "…"}${jobsOk ? "" : " · a job failed — see Settings"}`}
        />
      </div>
    </header>
  );
}
