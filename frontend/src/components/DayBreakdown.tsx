import { Link } from "react-router-dom";
import { usePerfDay } from "../api/hooks";
import type { DayBreakdown as Day, StatDay } from "../api/types";
import { fmtMoney, fmtPct, fmtQty, fmtSignedMoney, pnlClass } from "../lib/format";

const fmtDay = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-CA", {
    weekday: "short", year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
  });

const pct = (a: number | null, b: number | null) => (a && b != null ? (b / a - 1) * 100 : null);

/** Hover card for a Best/Worst day stat: what the portfolio, each position,
 *  the benchmarks and USD/CAD did that session, plus the day's trades. */
export function DayBreakdownPopover({ day }: { day: StatDay }) {
  const q = usePerfDay(day.date, day.prev, true);
  return (
    <div className="card w-[420px] p-3 text-[12px] shadow-lg">
      {q.isLoading ? (
        <div className="py-6 text-center text-muted">Loading {day.date}…</div>
      ) : q.isError || !q.data ? (
        <div className="py-6 text-center text-muted">Couldn't load this day's breakdown.</div>
      ) : (
        <DayBody d={q.data} />
      )}
    </div>
  );
}

function DayBody({ d }: { d: Day }) {
  const residual = d.total_pnl - d.rows.reduce((sum, r) => sum + r.pnl, 0);
  const fx = d.usdcad ? pct(d.usdcad.start, d.usdcad.end) : null;
  return (
    <>
      <div className="flex items-baseline justify-between">
        <div>
          <div className="font-medium">{fmtDay(d.date)}</div>
          <div className="text-[11px] text-muted">vs close of {fmtDay(d.prev)}{d.live && <span className="ml-1 text-up">· live</span>}</div>
        </div>
        <div className="text-right">
          <div className={`num text-[15px] font-semibold ${pnlClass(d.r)}`}>{fmtPct(d.r * 100)}</div>
          <div className={`num text-[11px] ${pnlClass(d.total_pnl)}`}>{fmtSignedMoney(d.total_pnl, "CAD")}</div>
        </div>
      </div>
      <div className="num mt-1 text-[11px] text-muted">
        {fmtMoney(d.opening_value, "CAD")} → {fmtMoney(d.closing_value, "CAD")}
        {d.flows !== 0 && <> · {d.flows > 0 ? "deposit" : "withdrawal"} {fmtMoney(Math.abs(d.flows), "CAD")} (not counted as return)</>}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border pt-2">
        <span className="text-[11px] uppercase tracking-wide text-muted">Market</span>
        {d.market.map((m) => (
          <span key={m.symbol} className="num">
            {m.symbol} <span className={pnlClass(m.r)}>{fmtPct(m.r * 100)}</span>
          </span>
        ))}
        {fx != null && (
          <span className="num">
            USD/CAD <span className="text-muted">{fmtPct(fx)}</span>
          </span>
        )}
      </div>

      <table className="mt-2 w-full border-t border-border">
        <thead className="text-[11px] text-muted">
          <tr>
            <th className="py-1 text-left font-medium">Position</th>
            <th className="py-1 text-right font-medium">Weight</th>
            <th className="py-1 text-right font-medium">Price</th>
            <th className="py-1 text-right font-medium">P&L (CAD)</th>
          </tr>
        </thead>
        <tbody>
          {d.rows.map((r) => {
            const move = pct(r.price_start, r.price_end);
            return (
              <tr key={r.ticker} className="border-t border-border/60">
                <td className="py-1">
                  <Link to={`/stocks/${encodeURIComponent(r.ticker)}`} className="font-medium hover:text-accent">{r.ticker}</Link>
                  <span className="num ml-1 text-[11px] text-muted">
                    {r.qty_start === r.qty_end ? fmtQty(r.qty_end) : `${fmtQty(r.qty_start)}→${fmtQty(r.qty_end)}`} sh
                  </span>
                </td>
                <td className="num py-1 text-right text-muted">
                  {d.opening_value ? fmtPct((r.mv_start / d.opening_value) * 100, false) : "—"}
                </td>
                <td className={`num py-1 text-right ${pnlClass(move)}`} title={`${r.price_start ?? "—"} → ${r.price_end ?? "—"} ${r.currency}`}>
                  {fmtPct(move)}
                </td>
                <td className={`num py-1 text-right ${pnlClass(r.pnl)}`}>{fmtSignedMoney(r.pnl, "CAD")}</td>
              </tr>
            );
          })}
          {Math.abs(residual) >= 0.01 && (
            <tr className="border-t border-border/60 text-muted">
              <td className="py-1" colSpan={3}>Cash, fees & rounding</td>
              <td className="num py-1 text-right">{fmtSignedMoney(residual, "CAD")}</td>
            </tr>
          )}
        </tbody>
      </table>

      {d.trades.length > 0 && (
        <div className="mt-2 border-t border-border pt-2">
          <div className="text-[11px] uppercase tracking-wide text-muted">Your trades</div>
          {d.trades.map((t, i) => (
            <div key={i} className="num">
              <span className={t.action === "buy" ? "text-up" : "text-down"}>{t.action.toUpperCase()}</span>{" "}
              {fmtQty(t.qty)} {t.ticker} @ {fmtMoney(t.price, t.currency === "USD" ? "USD" : "CAD")} {t.currency}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
