import type { RiskSummary } from "../api/types";
import { fmtFrac, fmtMoney, fmtNum } from "../lib/format";
import { StatCard } from "./ui";

/** The risk row: end-of-day figures for the current book, linked to the Risk tab. */
export function RiskRow({ r }: { r: RiskSummary }) {
  const cad = (f: number) => fmtMoney(f * r.total_cad, "CAD", 0);
  return (
    <div className="grid grid-cols-6 gap-2">
      <StatCard label="1-day VaR 95%" value={fmtFrac(r.var95, false)} sub={`${cad(r.var95)} · filtered historical`}
                title="A loss this book should exceed on about 1 day in 20, from two years of its own returns rescaled to today's volatility" />
      <StatCard label="Expected shortfall 97.5%" value={fmtFrac(r.es975, false)} sub={`${cad(r.es975)} · average of worst 2.5%`}
                title="The average loss on the worst 2.5% of days, the Basel FRTB measure" />
      <StatCard label="Volatility (ex-ante)" value={fmtFrac(r.vol, false)} sub="annualized · EWMA λ 0.94"
                title="Today's volatility of the current holdings, weighting recent sessions most" />
      <StatCard label="Beta" value={r.beta_spy != null ? fmtNum(r.beta_spy, 2) : "—"}
                sub={`to S&P 500 in CAD · TSX ${r.beta_xiu != null ? fmtNum(r.beta_xiu, 2) : "—"}`}
                title="How much the book moves per 1% move in the index, over the last year" />
      <StatCard label="Effective bets" value={r.enb != null ? fmtNum(r.enb, 1) : "—"}
                sub={`of ${r.holdings.length} positions · weight-based ${r.effective_n != null ? fmtNum(r.effective_n, 1) : "—"}`}
                title="Meucci's effective number of uncorrelated bets (minimum torsion). Lower than the position count means the risk is concentrated" />
      <StatCard label="Drawdown" value={fmtFrac(r.drawdown, false)} className={r.drawdown != null && r.drawdown < 0 ? "text-down" : ""}
                sub="from the time-weighted high" title="How far the time-weighted return index sits below its peak" />
    </div>
  );
}
