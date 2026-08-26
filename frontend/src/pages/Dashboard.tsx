import { Link } from "react-router-dom";
import { useHoldings, useSummary } from "../api/hooks";
import { useCurrency } from "../App";
import { AiAnalyst } from "../components/AiAnalyst";
import { AllocationDonut } from "../components/AllocationDonut";
import { HoldingsTable } from "../components/HoldingsTable";
import { fmtMoney, fmtPct, fmtSignedMoney, pnlClass } from "../lib/format";

function Stat({ label, value, className = "" }: { label: string; value: string; className?: string }) {
  return (
    <div className="card px-3 py-2">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className={`num mt-0.5 text-[17px] font-semibold ${className}`}>{value}</div>
    </div>
  );
}

export function Dashboard() {
  const { currency } = useCurrency();
  const summary = useSummary(currency);
  const holdings = useHoldings(currency);

  if (summary.isError || holdings.isError) {
    const err = (summary.error ?? holdings.error) as Error;
    return (
      <div className="card p-6 text-[13px]">
        <p className="text-down">Could not load portfolio data.</p>
        <p className="mt-1 text-muted">{err?.message}</p>
        <p className="mt-2 text-muted">
          Check the Questrade connection in{" "}
          <Link to="/settings" className="text-accent underline">
            Settings
          </Link>
          .
        </p>
      </div>
    );
  }

  if (summary.data?.empty) {
    return (
      <div className="card p-6 text-[13px]">
        <p>No tracked accounts yet.</p>
        <p className="mt-1 text-muted">
          Paste a Questrade refresh token and pick accounts in{" "}
          <Link to="/settings" className="text-accent underline">
            Settings
          </Link>
          .
        </p>
      </div>
    );
  }

  const s = summary.data;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-5 gap-2">
        <Stat label="Total value" value={s ? fmtMoney(s.total_value, currency) : "—"} />
        <Stat
          label="Day change"
          value={s ? `${fmtSignedMoney(s.day_pnl, currency)} (${fmtPct(s.day_pnl_pct)})` : "—"}
          className={pnlClass(s?.day_pnl)}
        />
        <Stat label="Open P&L" value={s ? fmtSignedMoney(s.open_pnl, currency) : "—"} className={pnlClass(s?.open_pnl)} />
        <Stat
          label={s?.mwr_since ? `MWR since ${s.mwr_since}` : "MWR since start"}
          value={s?.mwr != null ? fmtPct(s.mwr * 100) : "—"}
          className={pnlClass(s?.mwr)}
        />
        <Stat
          label="TWR YTD"
          value={s?.twr_ytd != null ? fmtPct(s.twr_ytd * 100) : "—"}
          className={pnlClass(s?.twr_ytd)}
        />
      </div>

      <div className="grid grid-cols-3 items-start gap-4">
        <section className="card col-span-2 overflow-hidden">
          <div className="flex items-baseline justify-between border-b border-border px-3 py-2">
            <h2 className="text-[13px] font-medium">Holdings</h2>
            {holdings.data && !holdings.data.empty && (
              <span className="num text-[11px] text-muted">
                cash {fmtMoney(holdings.data.cash_native?.CAD, "CAD")} · {fmtMoney(holdings.data.cash_native?.USD, "USD")}
              </span>
            )}
          </div>
          {holdings.isLoading ? (
            <p className="p-6 text-center text-[13px] text-muted">Loading…</p>
          ) : holdings.data && holdings.data.rows.length > 0 ? (
            <HoldingsTable rows={holdings.data.rows} />
          ) : (
            <p className="p-6 text-center text-[13px] text-muted">No open positions.</p>
          )}
        </section>
        <AllocationDonut />
      </div>

      <AiAnalyst />
    </div>
  );
}
