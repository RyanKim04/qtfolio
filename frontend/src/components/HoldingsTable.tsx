import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { HoldingRow, RiskHolding } from "../api/types";
import { useCurrency } from "../App";
import { fmtEtTime, fmtFrac, fmtMoney, fmtNum, fmtPct, fmtQty, fmtSignedMoney, pnlClass } from "../lib/format";
import { Sparkline } from "./Sparkline";

type SortKey =
  | keyof Pick<HoldingRow, "ticker" | "qty" | "avg_cost" | "last" | "market_value" | "weight" | "day_pnl" | "open_pnl" | "open_pnl_pct">
  | "risk_pct"
  | "beta";

type Row = HoldingRow & { risk_pct?: number | null; beta?: number | null; proxied?: number };

const COLUMNS: { key: SortKey; label: string; align?: "right"; title?: string }[] = [
  { key: "ticker", label: "Ticker" },
  { key: "qty", label: "Qty", align: "right" },
  { key: "avg_cost", label: "Avg cost", align: "right" },
  { key: "last", label: "Last", align: "right" },
  { key: "market_value", label: "Mkt value", align: "right" },
  { key: "weight", label: "Weight", align: "right" },
  { key: "risk_pct", label: "Risk", align: "right", title: "Share of portfolio volatility (Euler contribution, Ledoit-Wolf covariance over one year). Compare with Weight." },
  { key: "beta", label: "Beta", align: "right", title: "Beta to the S&P 500 in CAD over one year" },
  { key: "day_pnl", label: "Day P&L", align: "right" },
  { key: "open_pnl", label: "Open P&L", align: "right" },
  { key: "open_pnl_pct", label: "Open %", align: "right" },
];

export function HoldingsTable({ rows: base, risk }: { rows: HoldingRow[]; risk?: RiskHolding[] }) {
  const byAsset = new Map((risk ?? []).map((h) => [h.asset, h]));
  const rows: Row[] = base.map((r) => {
    const h = byAsset.get(r.ticker);
    return { ...r, risk_pct: h?.risk_pct ?? null, beta: h?.beta_spy ?? null, proxied: h?.proxied ?? 0 };
  });
  const { currency } = useCurrency();
  const navigate = useNavigate();
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "market_value", dir: -1 });

  const sorted = [...rows].sort((a, b) => {
    const av = a[sort.key];
    const bv = b[sort.key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = typeof av === "string" ? av.localeCompare(bv as string) : (av as number) - (bv as number);
    return cmp * sort.dir;
  });

  const clickSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: key === "ticker" ? 1 : -1 }));

  return (
    <table className="w-full border-collapse text-[13px]">
      <thead>
        <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
          {COLUMNS.map((c) => (
            <th
              key={c.key}
              title={c.title}
              onClick={() => clickSort(c.key)}
              className={`cursor-pointer select-none px-2 py-1.5 font-medium hover:text-text ${
                c.align === "right" ? "text-right" : "text-left"
              }`}
            >
              {c.label}
              {sort.key === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
            </th>
          ))}
          <th className="px-2 py-1.5 text-right font-medium">30d</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr
            key={r.ticker}
            onClick={() => navigate(`/stocks/${r.ticker}`)}
            className="cursor-pointer border-b border-border/50 hover:bg-bg"
          >
            <td className="px-2 py-1.5">
              <span className="num font-medium">{r.ticker}</span>
              <span className="num ml-1.5 text-[10px] text-muted">{r.currency}</span>
            </td>
            <td className="num px-2 py-1.5 text-right">{fmtQty(r.qty)}</td>
            <td className="num px-2 py-1.5 text-right">{fmtMoney(r.avg_cost, r.currency)}</td>
            <td className="num px-2 py-1.5 text-right">
              {r.session && (
                <span
                  className="mr-1 text-[10px] text-muted"
                  title={`${r.session === "post" ? "After-hours" : "Pre-market"} price · ${fmtEtTime(r.price_time)}`}
                >
                  {r.session === "post" ? "AH" : "PM"}
                </span>
              )}
              {fmtMoney(r.last, r.currency)}
            </td>
            <td className="num px-2 py-1.5 text-right">{fmtMoney(r.market_value, currency)}</td>
            <td className="num px-2 py-1.5 text-right text-muted">{fmtPct(r.weight, false)}</td>
            <td
              className={`num px-2 py-1.5 text-right ${r.risk_pct != null && r.weight != null && r.risk_pct * 100 > r.weight * 1.5 ? "font-medium text-text" : "text-muted"}`}
              title={r.proxied ? `${Math.round(r.proxied * 100)}% of the year is proxied from its factor model (short history)` : undefined}
            >
              {fmtFrac(r.risk_pct, false, 1)}
              {r.proxied ? "*" : ""}
            </td>
            <td className="num px-2 py-1.5 text-right text-muted">{r.beta != null ? fmtNum(r.beta, 2) : "—"}</td>
            <td className={`num px-2 py-1.5 text-right ${pnlClass(r.day_pnl)}`}>{fmtSignedMoney(r.day_pnl, currency)}</td>
            <td className={`num px-2 py-1.5 text-right ${pnlClass(r.open_pnl)}`}>{fmtSignedMoney(r.open_pnl, currency)}</td>
            <td className={`num px-2 py-1.5 text-right ${pnlClass(r.open_pnl_pct)}`}>{fmtPct(r.open_pnl_pct)}</td>
            <td className="px-2 py-1.5">
              <div className="flex justify-end">
                <Sparkline data={r.sparkline} />
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
