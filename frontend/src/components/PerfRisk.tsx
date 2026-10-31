import { Fragment, useMemo, useState } from "react";
import { Area, AreaChart, ComposedChart, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useDecisions, useEvent } from "../api/hooks";
import type { PerfMetrics, RangeKey } from "../api/types";
import { fmtFrac, fmtMoney, fmtNum, fmtSignedMoney, pnlClass } from "../lib/format";
import { COLORS, TOOLTIP_STYLE } from "../lib/palette";
import { fmtFullDate, makeTimeAxis } from "../lib/timeAxis";
import { Section } from "./ui";

const axisTick = { fontSize: 11, fill: "var(--color-muted)" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function DrawdownChart({ m }: { m: PerfMetrics }) {
  const rows = m.underwater.dates.map((d, i) => ({ ts: new Date(d).getTime(), dd: m.underwater.drawdown[i] * 100 }));
  const axis = makeTimeAxis(m.underwater.dates);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} ticks={axis.ticks} tickFormatter={axis.fmt}
               tick={axisTick} axisLine={{ stroke: "var(--color-border)" }} tickLine={false} />
        <YAxis width={48} tickFormatter={(v: number) => `${v.toFixed(0)}%`} tick={axisTick} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(ts: number) => fmtFullDate(ts)}
                 formatter={(v: number) => [`${v.toFixed(2)}%`, "Below the high"]} />
        <Area dataKey="dd" stroke="var(--color-down)" strokeWidth={1.5} fill="var(--color-down)" fillOpacity={0.12} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

const ROLLING = [
  { key: "vol", label: "Volatility", fmt: (v: number) => `${(v * 100).toFixed(0)}%` },
  { key: "sharpe", label: "Sharpe", fmt: (v: number) => v.toFixed(1) },
  { key: "beta", label: "Beta", fmt: (v: number) => v.toFixed(2) },
] as const;

/** Three small multiples rather than one chart with three scales. */
export function RollingChart({ m }: { m: PerfMetrics }) {
  const axis = makeTimeAxis(m.rolling.dates);
  return (
    <div className="flex h-full flex-col gap-1">
      {ROLLING.map((r, i) => {
        const vals = m.rolling[r.key];
        if (!vals) return null;
        const rows = m.rolling.dates.map((d, j) => ({ ts: new Date(d).getTime(), v: vals[j] }));
        return (
          <div key={r.key} className="min-h-0 flex-1">
            <div className="text-[11px] text-muted">
              {r.label} · {m.rolling.window}-session window{r.key === "beta" ? ` · vs ${m.benchmark} in CAD` : ""}
            </div>
            <ResponsiveContainer width="100%" height="85%">
              <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 8 }} syncId="rolling">
                <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} ticks={axis.ticks} tickFormatter={axis.fmt}
                       tick={i === ROLLING.length - 1 ? axisTick : false} axisLine={{ stroke: "var(--color-border)" }} tickLine={false}
                       height={i === ROLLING.length - 1 ? 20 : 4} />
                <YAxis width={48} tickFormatter={r.fmt} tick={axisTick} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
                {r.key !== "vol" && <ReferenceLine y={r.key === "beta" ? 1 : 0} stroke="var(--color-border)" />}
                <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(ts: number) => fmtFullDate(ts)} formatter={(v: number) => [r.fmt(v), r.label]} />
                <Line dataKey="v" stroke={COLORS[i]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </div>
        );
      })}
    </div>
  );
}

function Metric({ label, value, tip, cls = "", dim = false }: { label: string; value: string; tip: string; cls?: string; dim?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-b border-border/50 py-1" title={tip}>
      <span className="truncate text-muted underline decoration-border decoration-dotted underline-offset-2">{label}</span>
      <span className={`num ${cls} ${dim ? "opacity-60" : ""}`}>{value}</span>
    </div>
  );
}

export function MetricsPanel({ m, benchmarks, benchmark, onBenchmark }: {
  m: PerfMetrics; benchmarks: string[]; benchmark: string; onBenchmark: (b: string) => void;
}) {
  const s = m.stats;
  const b = s.benchmark;
  const n2 = (v: number | null | undefined) => (v == null ? "—" : fmtNum(v, 2));
  const psrWeak = s.psr != null && s.psr < 0.95;
  return (
    <Section
      title="Risk-adjusted performance"
      note={`Time-weighted, market sessions only (${s.n}), ${m.start} to ${m.end}. Risk-free rate: Bank of Canada 3-month T-bill, averaging ${fmtFrac(s.rf_annual, false)}.`}
      right={
        <label className="flex items-center gap-1.5 text-[12px] text-muted">
          vs
          <select value={benchmark} onChange={(e) => onBenchmark(e.target.value)}
                  className="rounded-md border border-border bg-panel px-1.5 py-0.5 text-[12px] text-text">
            {benchmarks.map((x) => <option key={x}>{x}</option>)}
          </select>
        </label>
      }
    >
      <div className="grid grid-cols-4 gap-x-6 px-3 py-2 text-[12px]">
        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Return</div>
          <Metric label="Total (TWR)" value={fmtFrac(s.total_return)} cls={pnlClass(s.total_return)} tip="Time-weighted return over the range" />
          <Metric label={s.annualized ? "Annualized (CAGR)" : "Annualized pace"} value={fmtFrac(s.cagr)} cls={pnlClass(s.cagr)} dim={!s.annualized}
                  tip={s.annualized ? "Compound annual growth rate" : "Under a year of history: an annual pace, not a result"} />
          <Metric label="Up days" value={fmtFrac(s.hit_rate, false, 0)} tip="Share of sessions with a positive return" />
          <Metric label="Skew / excess kurtosis" value={`${n2(s.skew)} / ${n2(s.excess_kurtosis)}`}
                  tip="Negative skew and high kurtosis mean fatter loss tails than volatility suggests" />
        </div>
        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Risk</div>
          <Metric label="Volatility (ann.)" value={fmtFrac(s.vol, false)} tip="Standard deviation of daily returns × √252" />
          <Metric label="Downside deviation" value={fmtFrac(s.downside_dev, false)} tip="Volatility of returns below the risk-free rate only" />
          <Metric label="Max drawdown" value={fmtFrac(s.max_drawdown, false)} cls="text-down"
                  tip={`Peak ${s.peak} to trough ${s.trough}${s.recovered ? `, recovered ${s.recovered}` : ", not yet recovered"}`} />
          <Metric label="Longest under water" value={s.longest_underwater_days != null ? `${s.longest_underwater_days} days` : "—"}
                  tip="Longest stretch below a previous high, calendar days" />
          <Metric label="Ulcer index" value={fmtFrac(s.ulcer_index, false)} tip="Root-mean-square drawdown: depth and duration of losses together" />
          <Metric label="1-day VaR / shortfall 95%" value={`${fmtFrac(s.var95_hist, false)} / ${fmtFrac(s.es95_hist, false)}`}
                  tip="Historical, on realized returns in this range" />
        </div>
        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Risk-adjusted</div>
          <Metric label="Sharpe" value={n2(s.sharpe)} tip="Annualized excess return per unit of volatility" />
          <Metric label="Sortino" value={n2(s.sortino)} tip="Excess return per unit of downside deviation" />
          <Metric label="Calmar" value={n2(s.calmar)} tip="Annualized return over max drawdown" />
          <Metric label="Omega (0)" value={n2(s.omega)} tip="Sum of gains over sum of losses, daily" />
          <Metric label="Probabilistic Sharpe" value={fmtFrac(s.psr, false, 0)} cls={psrWeak ? "text-muted" : ""}
                  tip="Bailey & López de Prado: the probability the true Sharpe is above 0, given track length, skew and fat tails" />
          <Metric label="Track record needed" value={s.min_trl_years != null ? `${fmtNum(s.min_trl_years, 1)} y (have ${fmtNum(s.track_years, 1)})` : "—"}
                  tip="Minimum track record length for 95% confidence that the Sharpe is above 0" />
        </div>
        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">Vs {m.benchmark} (CAD)</div>
          {b ? (
            <>
              <Metric label={`${m.benchmark} return`} value={fmtFrac(b.total_return)} cls={pnlClass(b.total_return)} tip="Benchmark total return in CAD over the same sessions" />
              <Metric label="Beta" value={n2(b.beta)} tip="Sensitivity to the benchmark's daily moves" />
              <Metric label="Alpha (Jensen, ann.)" value={fmtFrac(b.alpha)} cls={pnlClass(b.alpha)} tip="Excess return beyond what beta to the benchmark explains" />
              <Metric label="Correlation" value={n2(b.correlation)} tip="Daily return correlation" />
              <Metric label="Tracking error" value={fmtFrac(b.tracking_error, false)} tip="Annualized volatility of the return difference" />
              <Metric label="Information ratio" value={n2(b.information_ratio)} tip="Annualized active return over tracking error" />
              <Metric label="Up / down capture" value={`${fmtFrac(b.up_capture, false, 0)} / ${fmtFrac(b.down_capture, false, 0)}`}
                      tip="Average daily return on the benchmark's up days (and down days) relative to the benchmark's. Ideal: high up, low down" />
            </>
          ) : (
            <p className="text-muted">No {m.benchmark} history from Yahoo for this range.</p>
          )}
        </div>
      </div>
    </Section>
  );
}

// Monthly returns are P&L, so they take the reserved up/down hues, at a
// strength proportional to the size of the month (saturating at ±15%).
function cellColor(r: number) {
  const a = Math.min(1, Math.abs(r) / 0.15) * 0.55 + 0.08;
  return r >= 0 ? `rgba(23,128,61,${a})` : `rgba(223,27,65,${a})`;
}

export function MonthlyHeatmap({ m }: { m: PerfMetrics }) {
  const years = [...new Set(m.monthly.map((x) => x.year))].sort();
  if (!years.length) return null;
  const byKey = new Map(m.monthly.map((x) => [`${x.year}-${x.month}`, x.r]));
  return (
    <Section title="Monthly returns" note="Time-weighted, CAD. Year = compounded months in range.">
      <div className="overflow-x-auto p-3">
        <div className="grid gap-[2px] text-[11px]" style={{ gridTemplateColumns: "48px repeat(12, minmax(44px,1fr)) 60px" }}>
          <div />
          {MONTHS.map((x) => <div key={x} className="text-center text-muted">{x}</div>)}
          <div className="text-center font-medium text-muted">Year</div>
          {years.map((y) => {
            const ms = m.monthly.filter((x) => x.year === y);
            const yr = ms.reduce((t, x) => t * (1 + x.r), 1) - 1;
            return (
              <Fragment key={y}>
                <div className="num leading-7 text-muted">{y}</div>
                {MONTHS.map((_, i) => {
                  const r = byKey.get(`${y}-${i + 1}`);
                  return (
                    <div key={i} className="num flex h-7 items-center justify-center rounded-[3px]"
                         style={{ background: r == null ? "transparent" : cellColor(r) }}
                         title={r == null ? undefined : `${MONTHS[i]} ${y}: ${fmtFrac(r)}`}>
                      {r == null ? "" : (r * 100).toFixed(1)}
                    </div>
                  );
                })}
                <div className={`num flex h-7 items-center justify-center font-medium ${pnlClass(yr)}`}>{fmtFrac(yr, true, 1)}</div>
              </Fragment>
            );
          })}
        </div>
      </div>
    </Section>
  );
}

function EventStudy({ date }: { date: string }) {
  const q = useEvent(date);
  const e = q.data && !q.data.empty ? q.data : null;
  const rows = useMemo(
    () => (e ? e.dates.map((d, i) => ({ ts: new Date(d).getTime(), car: e.car[i] * 100, band: [e.lo[i] * 100, e.hi[i] * 100] })) : []),
    [e],
  );
  if (q.isLoading) return <p className="p-3 text-[12px] text-muted">Fitting the counterfactual…</p>;
  if (q.data?.empty) return <p className="p-3 text-[12px] text-muted">{q.data.reason}.</p>;
  if (!e) return null;
  const axis = makeTimeAxis(e.dates);
  const last = e.car[e.car.length - 1];
  return (
    <div className="grid grid-cols-3 gap-3 p-3">
      <div className="col-span-2" style={{ height: 160 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} ticks={axis.ticks} tickFormatter={axis.fmt}
                   tick={axisTick} axisLine={{ stroke: "var(--color-border)" }} tickLine={false} />
            <YAxis width={44} tickFormatter={(v: number) => `${v.toFixed(0)}%`} tick={axisTick} axisLine={false} tickLine={false} />
            <ReferenceLine y={0} stroke="var(--color-border)" />
            <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(ts: number) => fmtFullDate(ts)}
                     formatter={(v: number | number[]) => (Array.isArray(v) ? [`${v[0].toFixed(1)}% to ${v[1].toFixed(1)}%`, "90% band"] : [`${v.toFixed(2)}%`, "Abnormal return"])} />
            <Area dataKey="band" stroke="none" fill="var(--color-accent)" fillOpacity={0.12} isAnimationActive={false} />
            <Line dataKey="car" stroke="var(--color-accent)" strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[12px]">
        <div className="text-[11px] uppercase tracking-wide text-muted">Abnormal return after {e.date}</div>
        <div className={`num text-[17px] font-semibold ${pnlClass(last)}`}>{fmtFrac(last)}</div>
        <p className="mt-1 text-muted">
          Versus a counterfactual fitted on the {e.pre} sessions before (local level plus {e.regressors.join(", ")} factor returns),
          over {e.post} sessions. p = {e.p_value.toFixed(3)}.
        </p>
        {e.placebo && (
          <p className="mt-1 text-muted">
            Placebo on a fake date 40 sessions earlier: {fmtFrac(e.placebo.car)} (p = {e.placebo.p_value.toFixed(2)}). A large placebo means the
            model drifts and the result above is weaker than it looks.
          </p>
        )}
      </div>
    </div>
  );
}

export function TradeDecisions({ range }: { range: RangeKey }) {
  const q = useDecisions(range);
  const [h, setH] = useState(60);
  const [open, setOpen] = useState<string | null>(null);
  const d = q.data;
  if (q.isLoading) return <Section title="Trade decisions"><p className="p-4 text-[13px] text-muted">Building counterfactuals…</p></Section>;
  if (!d || !d.rows.length) return null;
  const sum = d.summary[String(h)];
  return (
    <Section
      title="Trade decisions"
      note="Each day's trades against the counterfactual of not making them: buys earn what they returned, sells earn what they avoided (CAD, from that day's close). Placebo rank: where the result falls among the same trade started on every other session within about three years. Click a day for an event study."
      right={
        <div className="flex overflow-hidden rounded-md border border-border">
          {d.horizons.map((x) => (
            <button key={x} onClick={() => setH(x)} aria-pressed={h === x}
                    className={`px-2.5 py-1 text-[12px] ${h === x ? "bg-accent font-medium text-white" : "bg-panel text-muted hover:text-text"}`}>
              {x} sessions
            </button>
          ))}
        </div>
      }
    >
      {sum && (
        <div className="num flex gap-6 border-b border-border px-3 py-2 text-[12px]">
          <span>
            <span className="text-muted">Value added by {sum.n} decision days </span>
            <span className={`font-medium ${pnlClass(sum.value_add)}`}>{fmtSignedMoney(sum.value_add, "CAD")}</span>
          </span>
          <span><span className="text-muted">Average placebo rank </span>{fmtFrac(sum.mean_percentile, false, 0)}</span>
          <span title="Share of decisions that beat the median placebo; 50% is what random timing gets">
            <span className="text-muted">Beat random timing </span>{fmtFrac(sum.beat_median, false, 0)}
          </span>
        </div>
      )}
      <div className="max-h-96 overflow-auto">
        <table className="w-full border-collapse text-[13px]">
          <thead className="sticky top-0 bg-panel">
            <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
              <th className="px-2 py-1.5 text-left font-medium">Date</th>
              <th className="px-2 py-1.5 text-left font-medium">Trades</th>
              <th className="px-2 py-1.5 text-right font-medium">Traded</th>
              <th className="px-2 py-1.5 text-right font-medium">Value added</th>
              <th className="px-2 py-1.5 text-right font-medium">Placebo rank</th>
            </tr>
          </thead>
          <tbody>
            {d.rows.map((r) => {
              const x = r.horizons[String(h)];
              return (
                <Fragment key={r.date}>
                  <tr onClick={() => setOpen(open === r.date ? null : r.date)} className="cursor-pointer border-b border-border/50 hover:bg-bg">
                    <td className="num px-2 py-1.5">{r.date}</td>
                    <td className="num px-2 py-1.5 text-[12px] text-muted">
                      {r.legs.slice(0, 4).map((l) => `${l.side === "buy" ? "+" : "−"}${l.ticker}`).join("  ")}
                      {r.legs.length > 4 && ` +${r.legs.length - 4}`}
                    </td>
                    <td className="num px-2 py-1.5 text-right text-muted">{fmtMoney(r.gross_cad, "CAD", 0)}</td>
                    <td className={`num px-2 py-1.5 text-right ${x ? pnlClass(x.value_add) : "text-muted"}`}>
                      {x ? fmtSignedMoney(x.value_add, "CAD") : "not yet"}
                    </td>
                    <td className="num px-2 py-1.5 text-right text-muted">{x?.percentile != null ? fmtFrac(x.percentile, false, 0) : "—"}</td>
                  </tr>
                  {open === r.date && (
                    <tr className="border-b border-border bg-bg">
                      <td colSpan={5}>
                        <EventStudy date={r.date} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
