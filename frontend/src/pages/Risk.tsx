import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  Bar,
  BarChart,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  useCausalGraph,
  useIrf,
  useRiskCovariance,
  useRiskFactors,
  useRiskReverse,
  useRiskShock,
  useRiskStress,
  useRiskSummary,
  useRiskVar,
} from "../api/hooks";
import type { CausalGraph, CovMethod, DriverMeta, Empty, RiskWindow, StressResult } from "../api/types";
import { RiskRow } from "../components/RiskRow";
import { LoadError, Loading, Section, Seg } from "../components/ui";
import { fmtFrac, fmtMoney, fmtNum, fmtSignedMoney, fmtUnit, pnlClass } from "../lib/format";
import { TOOLTIP_STYLE } from "../lib/palette";
import { fmtFullDate, makeTimeAxis } from "../lib/timeAxis";

const WINDOWS: RiskWindow[] = ["6m", "1y", "2y"];
const METHODS: { key: CovMethod; label: string }[] = [
  { key: "lw", label: "Shrunk" },
  { key: "ewma", label: "EWMA" },
  { key: "sample", label: "Sample" },
  { key: "factor", label: "Factor" },
];
const METHOD_NOTE: Record<CovMethod, string> = {
  lw: "Ledoit-Wolf shrinkage of the correlations toward zero, the stable default",
  ewma: "exponentially weighted (λ 0.94, about an 11-session half-life): today's regime",
  sample: "plain sample covariance",
  factor: "13-factor ETF model plus each holding's own risk, which copes with short price histories",
};
const th = "px-2 py-1.5 text-right font-medium";
const td = "num px-2 py-1.5 text-right";
const axisTick = { fontSize: 11, fill: "var(--color-muted)" };

// Diverging scale for correlations: blue below zero, blurple above, gray at zero.
function mix(a: string, b: string, t: number) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return `rgb(${pa.map((v, i) => Math.round(v + (pb[i] - v) * t)).join(",")})`;
}
const corrColor = (r: number) => (r >= 0 ? mix("#f2f4f7", "#4f46e5", Math.min(1, r)) : mix("#f2f4f7", "#175cd3", Math.min(1, -r)));

function data<T extends { empty: false }>(q: { data?: T | Empty }): T | null {
  return q.data && !q.data.empty ? (q.data as T) : null;
}

// -- 2. Risk decomposition -------------------------------------------------

function Decomposition() {
  const [method, setMethod] = useState<CovMethod>("lw");
  const [win, setWin] = useState<RiskWindow>("1y");
  const q = useRiskCovariance(method, win);
  const c = data(q);
  const [stressed, setStressed] = useState(false);
  const maxPct = c ? Math.max(...c.rows.map((r) => Math.max(r.pct, r.weight)), 0.01) : 1;
  return (
    <>
      <Section
        title="Where the risk comes from"
        note={`Each holding's share of portfolio volatility (Euler decomposition), next to its weight. Covariance: ${METHOD_NOTE[method]}.`}
        right={
          <>
            <Seg value={method} options={METHODS} onChange={setMethod} />
            <Seg value={win} options={WINDOWS} onChange={setWin} />
          </>
        }
      >
        {q.isLoading ? <Loading what="covariance" /> : q.isError ? <LoadError error={q.error} /> : c && (
          <div className="grid grid-cols-5 items-start">
            <table className="col-span-3 w-full border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
                  <th className="px-2 py-1.5 text-left font-medium">Holding</th>
                  <th className={th}>Weight</th>
                  <th className={th} title="Annualized volatility in CAD">Vol</th>
                  <th className={th} title="Beta to this portfolio: above 1 adds risk faster than its weight">Beta to book</th>
                  <th className={th} title="Marginal contribution: added annual volatility per unit of weight">Marginal</th>
                  <th className={th}>Risk share</th>
                  <th className={th} title="Share of variance from this holding's minimum-torsion bet (uncorrelated version of it)">Own bet</th>
                </tr>
              </thead>
              <tbody>
                {c.rows.map((r) => (
                  <tr key={r.asset} className="border-b border-border/50">
                    <td className="num px-2 py-1.5 font-medium">
                      {r.asset}
                      {r.proxied > 0.05 && (
                        <span className="ml-1 text-[10px] text-muted" title={`${Math.round(r.proxied * 100)}% of the window is proxied from its factor model: it has a shorter price history`}>
                          {Math.round(r.proxied * 100)}% proxied
                        </span>
                      )}
                    </td>
                    <td className={`${td} text-muted`}>{fmtFrac(r.weight, false, 1)}</td>
                    <td className={td}>{fmtFrac(r.vol, false, 1)}</td>
                    <td className={td}>{fmtNum(r.beta, 2)}</td>
                    <td className={td}>{fmtFrac(r.mctr, false, 1)}</td>
                    <td className={`${td} font-medium`}>{fmtFrac(r.pct, false, 1)}</td>
                    <td className={`${td} text-muted`}>{fmtFrac(r.bet_share, false, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="col-span-2 border-l border-border px-3 py-2">
              <div className="mb-1 flex gap-3 text-[11px] text-muted">
                <span className="flex items-center gap-1"><i className="inline-block h-2 w-3 rounded-sm bg-[#c9d2dc]" />Weight</span>
                <span className="flex items-center gap-1"><i className="inline-block h-2 w-3 rounded-sm bg-accent" />Risk share</span>
              </div>
              {c.rows.map((r) => (
                <div key={r.asset} className="mb-1.5" title={`${r.asset}: weight ${fmtFrac(r.weight, false, 1)}, risk ${fmtFrac(r.pct, false, 1)}`}>
                  <div className="num text-[11px] text-muted">{r.asset}</div>
                  <div className="h-1.5 rounded-sm bg-[#c9d2dc]" style={{ width: `${(r.weight / maxPct) * 100}%` }} />
                  <div className="mt-0.5 h-1.5 rounded-sm bg-accent" style={{ width: `${(Math.max(r.pct, 0) / maxPct) * 100}%` }} />
                </div>
              ))}
              <dl className="num mt-3 grid grid-cols-2 gap-x-3 gap-y-1 border-t border-border pt-2 text-[12px]">
                <dt className="text-muted">Volatility (ann.)</dt><dd className="text-right">{fmtFrac(c.vol, false, 1)}</dd>
                <dt className="text-muted" title="Weighted average of holding vols over portfolio vol. 1 = no diversification">Diversification ratio</dt>
                <dd className="text-right">{fmtNum(c.diversification_ratio, 2)}</dd>
                <dt className="text-muted" title="Meucci: exp(entropy) of risk across minimum-torsion bets">Effective bets (torsion)</dt>
                <dd className="text-right">{fmtNum(c.enb_torsion, 1)}</dd>
                <dt className="text-muted" title="Meucci 2009: across principal components">Effective bets (PCA)</dt>
                <dd className="text-right">{fmtNum(c.enb_pca, 1)}</dd>
                <dt className="text-muted" title="1 / Σ weight², positions only">Effective positions</dt>
                <dd className="text-right">{fmtNum(c.effective_n, 1)}</dd>
              </dl>
              <p className="num mt-2 text-[11px] text-muted">{c.n} sessions, {c.start} to {c.end}</p>
            </div>
          </div>
        )}
      </Section>
      {c && (
        <Section
          title="Correlation"
          note={
            stressed
              ? `Only the ${c.stress_days} sessions when the S&P 500 (in CAD) had its worst 10% of days. Correlations usually rise in selloffs, which is when diversification is needed.`
              : `All ${c.n} sessions, clustered so holdings that move together sit together.`
          }
          right={
            <>
              <span className="num text-[11px] text-muted">
                average {fmtNum(stressed ? c.avg_stress_corr : c.avg_corr, 2)}
                {!stressed && ` · stress days ${fmtNum(c.avg_stress_corr, 2)}`}
              </span>
              <Seg value={stressed ? "stress" : "all"} options={[{ key: "all", label: "All days" }, { key: "stress", label: "Stress days" }]}
                   onChange={(v) => setStressed(v === "stress")} />
            </>
          }
        >
          <Heatmap assets={c.assets} m={stressed ? c.stress_corr : c.corr} />
        </Section>
      )}
    </>
  );
}

function Heatmap({ assets, m }: { assets: string[]; m: number[][] }) {
  const n = assets.length;
  return (
    <div className="overflow-x-auto p-3">
      <div className="grid gap-[2px]" style={{ gridTemplateColumns: `88px repeat(${n}, minmax(44px, 1fr))` }}>
        <div />
        {assets.map((a) => (
          <div key={a} className="num truncate pb-1 text-center text-[11px] text-muted" title={a}>{a}</div>
        ))}
        {assets.map((a, i) => (
          <div key={a} className="contents">
            <div className="num truncate pr-2 text-right text-[11px] leading-8 text-muted">{a}</div>
            {m[i].map((v, j) => (
              <div
                key={j}
                title={`${a} · ${assets[j]}: ${v.toFixed(2)}`}
                className="num flex h-8 items-center justify-center rounded-[3px] text-[11px]"
                style={{ background: i === j ? "#e3e8ee" : corrColor(v), color: Math.abs(v) > 0.55 && i !== j ? "#fff" : "var(--color-text)" }}
              >
                {i === j ? "" : v.toFixed(2)}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-end gap-2 text-[11px] text-muted">
        <span>−1</span>
        <div className="h-2 w-40 rounded-sm" style={{ background: "linear-gradient(90deg,#175cd3,#f2f4f7,#4f46e5)" }} />
        <span>+1</span>
      </div>
    </div>
  );
}

// -- 4. Factors ---------------------------------------------------------------

function Factors() {
  const [win, setWin] = useState<RiskWindow>("1y");
  const q = useRiskFactors(win);
  const f = data(q);
  return (
    <Section
      title="Factor exposures"
      note="Betas of each holding's CAD returns on ETF factor proxies (spreads, so each reads as its own bet), Newey-West t-stats. Faded: |t| < 2, not distinguishable from zero."
      right={<Seg value={win} options={WINDOWS} onChange={setWin} />}
    >
      {q.isLoading ? <Loading what="factor regressions" /> : q.isError ? <LoadError error={q.error} /> : f && (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr className="border-b border-border text-[10px] uppercase tracking-wide text-muted">
                <th className="px-2 py-1.5 text-left font-medium">Holding</th>
                {f.factors.map((x) => <th key={x.key} className="px-1.5 py-1.5 text-right font-medium" title={x.label}>{x.label}</th>)}
                <th className={th} title="Share of variance the factors explain">R²</th>
                <th className={th} title="Volatility the factors leave unexplained, annualized">Own vol</th>
              </tr>
            </thead>
            <tbody>
              {f.holdings.map((h) => (
                <tr key={h.asset} className="border-b border-border/50">
                  <td className="num px-2 py-1.5 font-medium">{h.asset}</td>
                  {f.factors.map((x) => {
                    const b = h.betas?.[x.key];
                    return (
                      <td key={x.key} className={`num px-1.5 py-1.5 text-right ${b && Math.abs(b.t) >= 2 ? "" : "text-muted/60"}`}
                          title={b ? `t = ${b.t.toFixed(1)}` : undefined}>
                        {b ? fmtNum(b.beta, 2) : "—"}
                      </td>
                    );
                  })}
                  <td className={td}>{h.r2 != null ? fmtFrac(h.r2, false, 0) : "—"}</td>
                  <td className={`${td} text-muted`}>{h.specific_vol != null ? fmtFrac(h.specific_vol, false, 0) : "—"}</td>
                </tr>
              ))}
              <tr className="border-t border-border bg-bg font-medium">
                <td className="px-2 py-1.5">Portfolio</td>
                {f.portfolio.factors.map((x) => <td key={x.key} className="num px-1.5 py-1.5 text-right">{fmtNum(x.exposure, 2)}</td>)}
                <td colSpan={2} />
              </tr>
              <tr className="text-muted">
                <td className="px-2 py-1.5 text-[11px]" title="Euler share of portfolio variance from each factor">Risk share</td>
                {f.portfolio.factors.map((x) => <td key={x.key} className="num px-1.5 py-1.5 text-right">{fmtFrac(x.risk_share, false, 0)}</td>)}
                <td colSpan={2} />
              </tr>
            </tbody>
          </table>
          <p className="num px-3 py-2 text-[11px] text-muted">
            Factors explain {fmtFrac(f.portfolio.factor_share, false, 0)} of the book's variance; the rest (
            {fmtFrac(f.portfolio.specific_share, false, 0)}) is specific to the holdings. {f.n} sessions.
          </p>
        </div>
      )}
    </Section>
  );
}

// -- 5. VaR -------------------------------------------------------------------

const VAR_METHODS = [
  ["fhs", "Filtered historical", "Two years of the current book's returns, devolatilized by EWMA and rescaled to today's volatility (Barone-Adesi)"],
  ["historical", "Historical", "Empirical quantile of the current book's returns over two years"],
  ["normal", "Normal", "Gaussian on the EWMA covariance"],
  ["cornish_fisher", "Cornish-Fisher", "Gaussian quantile corrected for the returns' skew and fat tails"],
  ["student_t", "Student-t", "Fat-tailed t with the tail index fitted to the returns"],
] as const;

function VarSection() {
  const [conf, setConf] = useState(0.95);
  const [horizon, setHorizon] = useState(1);
  const q = useRiskVar(conf, horizon);
  const v = data(q);
  const fhsVar = v?.methods.fhs.var ?? 0;
  const hist = useMemo(() => {
    if (!v) return [];
    return v.hist.counts.map((c, i) => {
      const mid = (v.hist.edges[i] + v.hist.edges[i + 1]) / 2;
      return { x: mid * 100, c, tail: mid <= -fhsVar / Math.sqrt(horizon) };
    });
  }, [v, fhsVar, horizon]);
  const bt = v?.backtest;
  const btRows = useMemo(() => {
    if (!bt) return [];
    return bt.dates.map((d, i) => {
      const r = bt.returns[i];
      const lim = -bt.fhs.var[i];
      return { ts: new Date(d).getTime(), r: r * 100, v: lim * 100, hit: r < lim ? r * 100 : null };
    });
  }, [bt]);
  const axis = makeTimeAxis(bt?.dates ?? []);
  const pv = (p: number | null | undefined) => (p == null ? "—" : p < 0.001 ? "<0.001" : p.toFixed(3));
  return (
    <Section
      title="Value at Risk and expected shortfall"
      note="Losses on the current book, five methods side by side. Longer horizons scale by √time."
      right={
        <>
          <Seg value={conf} options={[{ key: 0.95, label: "95%" }, { key: 0.975, label: "97.5%" }, { key: 0.99, label: "99%" }]} onChange={setConf} />
          <Seg value={horizon} options={[{ key: 1, label: "1 day" }, { key: 10, label: "10 days" }]} onChange={setHorizon} />
        </>
      }
    >
      {q.isLoading ? <Loading what="VaR" /> : q.isError ? <LoadError error={q.error} /> : v && (
        <div className="grid grid-cols-2">
          <div className="border-r border-border">
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
                  <th className="px-2 py-1.5 text-left font-medium">Method</th>
                  <th className={th}>VaR</th>
                  <th className={th}>CAD</th>
                  <th className={th}>Shortfall</th>
                  <th className={th}>CAD</th>
                </tr>
              </thead>
              <tbody>
                {VAR_METHODS.map(([k, label, tip]) => {
                  const m = v.methods[k];
                  return (
                    <tr key={k} className={`border-b border-border/50 ${k === "fhs" ? "font-medium" : ""}`}>
                      <td className="px-2 py-1.5 underline decoration-border decoration-dotted underline-offset-2" title={tip}>
                        {label}
                        {k === "student_t" && m.nu != null && <span className="num ml-1 text-[11px] text-muted">ν {m.nu.toFixed(1)}</span>}
                      </td>
                      <td className={td}>{fmtFrac(m.var, false)}</td>
                      <td className={`${td} text-muted`}>{m.var != null ? fmtMoney(m.var * v.total_cad, "CAD", 0) : "—"}</td>
                      <td className={td}>{fmtFrac(m.es, false)}</td>
                      <td className={`${td} text-muted`}>{m.es != null ? fmtMoney(m.es * v.total_cad, "CAD", 0) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="px-3 pb-2 pt-3">
              <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">By holding (Euler)</div>
              <table className="w-full border-collapse text-[12px]">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wide text-muted">
                    <th className="py-1 text-left font-medium">Holding</th>
                    <th className="py-1 text-right font-medium" title="Gaussian component VaR; sums to the normal VaR">Component VaR</th>
                    <th className="py-1 text-right font-medium" title="Average loss on the book's worst days; sums to historical shortfall">Shortfall share</th>
                  </tr>
                </thead>
                <tbody>
                  {v.components.map((c) => (
                    <tr key={c.asset} className="border-t border-border/50">
                      <td className="num py-1">{c.asset}</td>
                      <td className="num py-1 text-right">{fmtMoney(c.var * v.total_cad, "CAD", 0)}</td>
                      <td className="num py-1 text-right">{fmtMoney(c.es * v.total_cad, "CAD", 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="p-3">
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">
              Daily returns of the current book, two years
            </div>
            <div style={{ height: 170 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={hist} margin={{ top: 4, right: 8, bottom: 0, left: 0 }} barCategoryGap={1}>
                  <XAxis dataKey="x" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(x: number) => `${x.toFixed(0)}%`} tick={axisTick} axisLine={{ stroke: "var(--color-border)" }} tickLine={false} />
                  <YAxis hide />
                  <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(x: number) => `${x.toFixed(2)}%`} formatter={(c: number) => [c, "sessions"]} />
                  <Bar dataKey="c" isAnimationActive={false} radius={[2, 2, 0, 0]}>
                    {hist.map((h, i) => <Cell key={i} fill={h.tail ? "var(--color-accent)" : "#c9d2dc"} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            {bt ? (
              <>
                <div className="mb-1 mt-3 flex items-baseline justify-between text-[11px] text-muted">
                  <span className="font-medium uppercase tracking-wide">Backtest on realized returns</span>
                  <span className="num">
                    {bt.fhs.exceptions} breaches in {bt.fhs.n} sessions (expected {bt.fhs.expected.toFixed(1)})
                  </span>
                </div>
                <div style={{ height: 170 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <ComposedChart data={btRows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                      <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} ticks={axis.ticks} tickFormatter={axis.fmt} tick={axisTick} axisLine={{ stroke: "var(--color-border)" }} tickLine={false} />
                      <YAxis width={40} tickFormatter={(x: number) => `${x.toFixed(0)}%`} tick={axisTick} axisLine={false} tickLine={false} />
                      <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(ts: number) => fmtFullDate(ts)}
                               formatter={(x: number, name: string) => [`${x.toFixed(2)}%`, name === "r" ? "Return" : name === "v" ? "VaR limit" : "Breach"]} />
                      <Bar dataKey="r" fill="#c9d2dc" isAnimationActive={false} />
                      <Line dataKey="v" stroke="var(--color-accent)" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                      <Scatter dataKey="hit" fill="var(--color-accent-deep)" isAnimationActive={false} />
                    </ComposedChart>
                  </ResponsiveContainer>
                </div>
                <p className="num mt-1 text-[11px] text-muted">
                  One-day-ahead filtered historical VaR from the trailing 250 sessions. Kupiec coverage p = {pv(bt.fhs.kupiec.p_value)},
                  Christoffersen independence p = {pv(bt.fhs.christoffersen?.p_value)} (below 0.05 means the model misses).
                  Plain historical: {bt.historical.exceptions} breaches, p = {pv(bt.historical.kupiec.p_value)}.
                </p>
              </>
            ) : (
              <p className="mt-3 text-[11px] text-muted">The backtest needs 300+ sessions of realized history.</p>
            )}
          </div>
        </div>
      )}
    </Section>
  );
}

// -- 6. Stress ----------------------------------------------------------------

function Historical({ s }: { s: StressResult }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <table className="w-full border-collapse text-[13px]">
      <thead>
        <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
          <th className="px-2 py-1.5 text-left font-medium">Scenario</th>
          <th className="px-2 py-1.5 text-left font-medium">Window</th>
          <th className={th} title="S&P 500 total return over the window, USD">S&P 500</th>
          <th className={th}>USD/CAD</th>
          <th className={th}>This book</th>
          <th className={th}>CAD</th>
          <th className="px-2 py-1.5 text-left font-medium">Worst</th>
        </tr>
      </thead>
      <tbody>
        {s.scenarios.map((sc) => (
          <Fragment key={sc.key}>
            <tr onClick={() => setOpen(open === sc.key ? null : sc.key)} className="cursor-pointer border-b border-border/50 hover:bg-bg">
              <td className="px-2 py-1.5">
                {sc.label}
                {sc.factor_mapped.length > 0 && (
                  <span className="ml-1.5 text-[10px] text-muted" title={`Not trading then, so mapped through today's factor betas: ${sc.factor_mapped.join(", ")}`}>
                    {sc.factor_mapped.length} mapped
                  </span>
                )}
              </td>
              <td className="num px-2 py-1.5 text-[12px] text-muted">{sc.start} → {sc.end}</td>
              <td className={`${td} text-muted`}>{fmtFrac(sc.market, true, 1)}</td>
              <td className={`${td} text-muted`}>{fmtFrac(sc.usdcad, true, 1)}</td>
              <td className={`${td} font-medium ${pnlClass(sc.portfolio)}`}>{fmtFrac(sc.portfolio, true, 1)}</td>
              <td className={`${td} ${pnlClass(sc.pnl_cad)}`}>{fmtSignedMoney(sc.pnl_cad, "CAD", 0)}</td>
              <td className="num px-2 py-1.5 text-[12px] text-muted">{sc.worst ?? "—"}</td>
            </tr>
            {open === sc.key && (
              <tr className="border-b border-border bg-bg">
                <td colSpan={7} className="px-3 py-2">
                  <div className="num flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
                    {sc.holdings.map((h) => (
                      <span key={h.asset}>
                        {h.asset} <span className={pnlClass(h.return)}>{fmtFrac(h.return, true, 1)}</span>
                        <span className="ml-0.5 text-[10px] text-muted">{h.method === "factor" ? "mapped" : h.method === "replay" ? "" : "n/a"}</span>
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

function ShockLab({ drivers }: { drivers: DriverMeta[] }) {
  const [shocks, setShocks] = useState<Record<string, number>>({ rates: 50 });
  const q = useRiskShock(shocks);
  const r = data(q);
  const set = (k: string, v: number) => setShocks((s) => ({ ...s, [k]: v }));
  const maxCh = r ? Math.max(...r.causal.channels.map((c) => Math.abs(c.contribution)), 1e-9) : 1;
  return (
    <div className="grid grid-cols-5">
      <div className="col-span-2 border-r border-border p-3">
        <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">Intervene on drivers (one day)</div>
        {drivers.map((d) => (
          <div key={d.key} className="mb-1.5 flex items-center gap-2 text-[12px]">
            <label className="w-24 shrink-0 text-muted" htmlFor={`shock-${d.key}`}>{d.label}</label>
            <input
              id={`shock-${d.key}`}
              type="range"
              min={-2 * Math.abs(d.default)}
              max={2 * Math.abs(d.default)}
              step={Math.abs(d.default) / 10}
              value={shocks[d.key] ?? 0}
              onChange={(e) => set(d.key, Number(e.target.value))}
              className="min-w-0 flex-1 accent-(--color-accent)"
            />
            <span className="num w-16 text-right">{fmtUnit(shocks[d.key] ?? 0, d.unit)}</span>
          </div>
        ))}
        <div className="mt-2 flex gap-2 text-[11px]">
          <button onClick={() => setShocks(Object.fromEntries(drivers.map((d) => [d.key, d.key === "equity" ? d.default : 0])))}
                  className="rounded border border-border px-2 py-0.5 text-muted hover:text-text">Equity −10%</button>
          <button onClick={() => setShocks({ vix: 15, credit: -3 })} className="rounded border border-border px-2 py-0.5 text-muted hover:text-text">Risk-off</button>
          <button onClick={() => setShocks({ rates: 50, usd: 2 })} className="rounded border border-border px-2 py-0.5 text-muted hover:text-text">Hawkish</button>
          <button onClick={() => setShocks({})} className="rounded border border-border px-2 py-0.5 text-muted hover:text-text">Clear</button>
        </div>
      </div>
      <div className={`col-span-3 p-3 transition-opacity ${q.isPlaceholderData ? "opacity-50" : ""}`} aria-busy={q.isFetching}>
        {Object.values(shocks).every((v) => !v) ? (
          <p className="py-8 text-center text-[13px] text-muted">Move a slider to shock a driver.</p>
        ) : q.isError ? <LoadError error={q.error} /> : !r ? <Loading /> : (
          <>
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-md border border-border px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-muted" title="Pearl's do(): the shocked drivers are set and only their consequences move">Causal effect</div>
                <div className={`num text-[17px] font-semibold ${pnlClass(r.causal.portfolio)}`}>{fmtFrac(r.causal.portfolio, true)}</div>
                <div className="num text-[11px] text-muted">
                  {fmtSignedMoney(r.causal.portfolio * r.total_cad, "CAD", 0)}
                  {r.causal.ci90 && ` · 90% CI ${fmtFrac(r.causal.ci90[0], true, 1)} to ${fmtFrac(r.causal.ci90[1], true, 1)}`}
                </div>
              </div>
              <div className="rounded-md border border-border px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-muted">Direct / through other drivers</div>
                <div className="num text-[15px] font-semibold">
                  <span className={pnlClass(r.causal.direct)}>{fmtFrac(r.causal.direct, true, 1)}</span>
                  <span className="text-muted"> / </span>
                  <span className={pnlClass(r.causal.indirect)}>{fmtFrac(r.causal.indirect, true, 1)}</span>
                </div>
                <div className="text-[11px] text-muted">indirect = the incremental knock-on</div>
              </div>
              <div className="rounded-md border border-border px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-muted" title="E[book | driver = x]: also absorbs whatever usually moves with the shocked driver">Correlational answer</div>
                <div className={`num text-[15px] font-semibold ${pnlClass(r.correlational.portfolio)}`}>{fmtFrac(r.correlational.portfolio, true)}</div>
                <div className="num text-[11px] text-muted">
                  gap {fmtFrac(r.correlational.portfolio - r.causal.portfolio, true, 1)} = confounding
                </div>
              </div>
            </div>
            <div className="mb-1 mt-3 text-[11px] font-medium uppercase tracking-wide text-muted">Channels (path-specific effects)</div>
            {r.causal.channels.slice(0, 8).map((c, i) => (
              <div key={i} className="mb-1 flex items-center gap-2 text-[12px]">
                <span className="w-64 shrink-0 truncate text-muted" title={c.path.join(" → ")}>{[...c.path, "holdings"].join(" → ")}</span>
                <div className="relative h-2 flex-1">
                  <div className="absolute top-0 h-2 w-px bg-border" style={{ left: "50%" }} />
                  <div className={`absolute top-0 h-2 rounded-sm ${c.contribution >= 0 ? "bg-up" : "bg-down"}`}
                       style={{ left: c.contribution >= 0 ? "50%" : `${50 - (Math.abs(c.contribution) / maxCh) * 50}%`, width: `${(Math.abs(c.contribution) / maxCh) * 50}%` }} />
                </div>
                <span className={`num w-16 text-right ${pnlClass(c.contribution)}`}>{fmtFrac(c.contribution, true)}</span>
              </div>
            ))}
            <div className="num mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
              {r.drivers.filter((d) => !d.shocked).map((d) => (
                <span key={d.key} title="Implied move: causal (correlational)">{d.label} {fmtUnit(d.causal, d.unit)} ({fmtUnit(d.correlational, d.unit)})</span>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ReverseStress() {
  const [loss, setLoss] = useState(0.2);
  const [horizon, setHorizon] = useState(21);
  const q = useRiskReverse(loss, horizon);
  const r = data(q);
  const years = (y: number | null) =>
    y == null ? "—"
      : y < 1 ? "more than once a year"
      : y > 1000 ? "rarer than once in 1,000 years"
      : `about once every ${y < 10 ? y.toFixed(1) : Math.round(y).toLocaleString()} years`;
  return (
    <div className="p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-[12px] text-muted">Most plausible way to lose</span>
        <Seg value={loss} options={[{ key: 0.1, label: "10%" }, { key: 0.2, label: "20%" }, { key: 0.3, label: "30%" }]} onChange={setLoss} />
        <span className="text-[12px] text-muted">over</span>
        <Seg value={horizon} options={[{ key: 1, label: "1 day" }, { key: 21, label: "1 month" }, { key: 63, label: "3 months" }]} onChange={setHorizon} />
      </div>
      {q.isError ? <LoadError error={q.error} /> : !r ? <Loading /> : (
        <div className={`grid grid-cols-2 gap-4 text-[12px] ${q.isPlaceholderData ? "opacity-50" : ""}`} aria-busy={q.isFetching}>
          <div>
            <div className="mb-1 font-medium">Macro drivers</div>
            {r.drivers.map((d) => (
              <div key={d.key} className="num flex justify-between border-b border-border/50 py-0.5">
                <span className="text-muted">{d.label}</span><span>{fmtUnit(d.move, d.unit)}</span>
              </div>
            ))}
            <p className="mt-1 text-[11px] text-muted">
              {r.driver_distance != null && `${r.driver_distance.toFixed(1)} standard deviations, ${years(r.driver_every_years)}. `}
              Drivers explain {fmtFrac(r.driver_r2, false, 0)} of the book's daily variance.
            </p>
          </div>
          <div>
            <div className="mb-1 font-medium">Holdings</div>
            {r.assets.map((a) => (
              <div key={a.asset} className="num flex justify-between border-b border-border/50 py-0.5">
                <span className="text-muted">{a.asset}</span><span className={pnlClass(a.move)}>{fmtFrac(a.move, true, 1)}</span>
              </div>
            ))}
            <p className="mt-1 text-[11px] text-muted">
              {r.asset_distance != null && `${r.asset_distance.toFixed(1)} standard deviations of the book, ${years(r.asset_every_years)} under normal tails (fat tails make it more often).`}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function MonteCarloView({ s }: { s: StressResult }) {
  const mc = s.monte_carlo;
  const rows = mc.hist.counts.map((c, i) => ({ x: ((mc.hist.edges[i] + mc.hist.edges[i + 1]) / 2) * 100, c }));
  return (
    <div className="grid grid-cols-3 gap-3 p-3">
      <div className="col-span-2" style={{ height: 170 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }} barCategoryGap={1}>
            <XAxis dataKey="x" type="number" domain={["dataMin", "dataMax"]} tickFormatter={(x: number) => `${x.toFixed(0)}%`} tick={axisTick} axisLine={{ stroke: "var(--color-border)" }} tickLine={false} />
            <YAxis hide />
            <ReferenceLine x={-mc.var95 * 100} stroke="var(--color-accent)" strokeDasharray="3 3" />
            <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(x: number) => `${x.toFixed(1)}%`} formatter={(c: number) => [c, "paths"]} />
            <Bar dataKey="c" isAnimationActive={false} radius={[2, 2, 0, 0]}>
              {rows.map((h, i) => <Cell key={i} fill={h.x <= -mc.var95 * 100 ? "var(--color-accent)" : "#c9d2dc"} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <dl className="num grid grid-cols-2 content-start gap-x-3 gap-y-1 text-[12px]">
        <dt className="text-muted">Median month</dt><dd className={`text-right ${pnlClass(mc.median)}`}>{fmtFrac(mc.median, true, 1)}</dd>
        <dt className="text-muted">VaR 95% / 99%</dt><dd className="text-right">{fmtFrac(mc.var95, false, 1)} / {fmtFrac(mc.var99, false, 1)}</dd>
        <dt className="text-muted">Shortfall 95% / 99%</dt><dd className="text-right">{fmtFrac(mc.es95, false, 1)} / {fmtFrac(mc.es99, false, 1)}</dd>
        <dt className="text-muted">Chance of losing 10%+</dt><dd className="text-right">{fmtFrac(mc.p_loss_10, false, 1)}</dd>
        <dt className="text-muted">Chance of losing 20%+</dt><dd className="text-right">{fmtFrac(mc.p_loss_20, false, 1)}</dd>
        <dd className="col-span-2 mt-1 text-[11px] text-muted">
          {mc.paths.toLocaleString()} paths of {mc.days} sessions: whole past days resampled (keeping how holdings crash together), scaled to today's volatilities.
        </dd>
      </dl>
    </div>
  );
}

function StressSection() {
  const q = useRiskStress();
  const s = data(q);
  const [tab, setTab] = useState<"historical" | "shock" | "reverse" | "mc">("historical");
  const notes = {
    historical: "Past crises replayed on today's book in CAD. Holdings that didn't trade then are mapped through today's factor betas, compounded daily. Click a row for holdings.",
    shock: "A structural causal model of the macro drivers: a shock is an intervention, and its effect flows only down the graph, split into direct and knock-on channels. Compare with the correlational answer most stress tools give.",
    reverse: "Reverse stress test: the smallest (Mahalanobis) move that produces the loss, from the drivers' and holdings' joint covariance.",
    mc: "One month ahead by filtered historical simulation.",
  };
  return (
    <Section
      title="Stress tests"
      note={notes[tab]}
      right={<Seg value={tab} options={[{ key: "historical", label: "Historical" }, { key: "shock", label: "Shock" }, { key: "reverse", label: "Reverse" }, { key: "mc", label: "Monte Carlo" }]} onChange={setTab} />}
    >
      {q.isLoading ? <Loading what="stress tests" /> : q.isError ? <LoadError error={q.error} /> : s && (
        tab === "historical" ? <Historical s={s} /> : tab === "shock" ? <ShockLab drivers={s.drivers} /> : tab === "reverse" ? <ReverseStress /> : <MonteCarloView s={s} />
      )}
    </Section>
  );
}

// -- 7. Causal ----------------------------------------------------------------

const LAYERS: Record<string, number> = { rates: 0, oil: 0, usd: 1, credit: 1, vix: 2, equity: 3, usdcad: 4, portfolio: 5 };

function Dag({ g }: { g: CausalGraph }) {
  const W = 760, H = 260, pad = 60;
  const byLayer: Record<number, string[]> = {};
  g.nodes.forEach((n) => (byLayer[LAYERS[n.key] ?? 0] ??= []).push(n.key));
  const pos: Record<string, { x: number; y: number }> = {};
  Object.entries(byLayer).forEach(([l, keys]) => {
    keys.forEach((k, i) => {
      pos[k] = { x: pad + (Number(l) * (W - 2 * pad)) / 5, y: (H * (i + 1)) / (keys.length + 1) };
    });
  });
  const label = Object.fromEntries(g.nodes.map((n) => [n.key, n.label]));
  const found = new Set(g.discovery.contemporaneous.map((e) => [e.a, e.b].sort().join("|")));
  const impact = Object.fromEntries(g.impacts.map((i) => [i.key, i.direct]));
  const maxImp = Math.max(...g.impacts.map((i) => Math.abs(i.direct)), 1e-9);
  const edges = [
    ...g.edges.map((e) => ({ from: e.from, to: e.to, w: Math.min(1, Math.abs(e.std_coef ?? 0)), neg: (e.coef ?? 0) < 0,
      tip: `${label[e.from]} → ${label[e.to]}: standardized ${fmtNum(e.std_coef, 2)}` })),
    ...g.impacts.map((i) => ({ from: i.key, to: "portfolio", w: Math.abs(impact[i.key]) / maxImp, neg: impact[i.key] < 0,
      tip: `${label[i.key]} → portfolio directly: ${fmtFrac(i.direct, true)} per typical day's move` })),
  ];
  const unexpected = g.discovery.unexpected.filter(([a, b]) => !edges.some((e) => [e.from, e.to].sort().join("|") === [a, b].join("|")));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Causal graph of macro drivers and the portfolio">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="#98a2b3" />
        </marker>
      </defs>
      {edges.map((e, i) => {
        const a = pos[e.from], b = pos[e.to];
        if (!a || !b) return null;
        const conf = found.has([e.from, e.to].sort().join("|"));
        const mx = (a.x + b.x) / 2;
        return (
          <path key={i} d={`M${a.x + 38},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x - 40},${b.y}`} fill="none"
                stroke={e.neg ? "#175cd3" : "#635bff"} strokeOpacity={conf ? 0.9 : 0.35}
                strokeWidth={0.75 + 3.5 * e.w} strokeDasharray={conf ? undefined : "4 3"} markerEnd="url(#arr)">
            <title>{e.tip}{conf ? " · confirmed by the data" : " · not found by the independence tests"}</title>
          </path>
        );
      })}
      {unexpected.map(([a, b], i) => pos[a] && pos[b] && (
        <line key={`u${i}`} x1={pos[a].x} y1={pos[a].y} x2={pos[b].x} y2={pos[b].y} stroke="#98a2b3" strokeDasharray="1 3" strokeWidth={1.5}>
          <title>{label[a]} and {label[b]} are linked in the data but not in the prior graph</title>
        </line>
      ))}
      {g.nodes.map((n) => (
        <g key={n.key}>
          <rect x={pos[n.key].x - 40} y={pos[n.key].y - 13} width={80} height={26} rx={6}
                fill={n.key === "portfolio" ? "var(--color-accent)" : "var(--color-panel)"} stroke="var(--color-border)" />
          <text x={pos[n.key].x} y={pos[n.key].y + 4} textAnchor="middle" fontSize={11}
                fill={n.key === "portfolio" ? "#fff" : "var(--color-text)"}>{n.label}</text>
        </g>
      ))}
    </svg>
  );
}

function Irf({ drivers }: { drivers: { key: string; label: string }[] }) {
  const [driver, setDriver] = useState("equity");
  const q = useIrf(driver);
  const r = data(q);
  const rows = r ? r.portfolio.beta.map((b, h) => ({ h, b: b * 100, band: [r.portfolio.lo[h] * 100, r.portfolio.hi[h] * 100] })) : [];
  return (
    <div className="p-3">
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {drivers.map((d) => (
          <button key={d.key} onClick={() => setDriver(d.key)}
                  className={`rounded-full border px-2 py-0.5 text-[11px] ${driver === d.key ? "border-accent bg-accent-soft text-accent" : "border-border text-muted hover:text-text"}`}>
            {d.label}
          </button>
        ))}
      </div>
      {q.isError ? <LoadError error={q.error} /> : !r ? <Loading /> : (
        <div className={q.isPlaceholderData ? "opacity-50" : ""} aria-busy={q.isFetching}>
          <div style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <XAxis dataKey="h" tick={axisTick} axisLine={{ stroke: "var(--color-border)" }} tickLine={false}
                       label={{ value: "sessions after the shock", position: "insideBottomRight", offset: -2, fontSize: 10, fill: "var(--color-muted)" }} />
                <YAxis width={44} tickFormatter={(x: number) => `${x.toFixed(1)}%`} tick={axisTick} axisLine={false} tickLine={false} />
                <ReferenceLine y={0} stroke="var(--color-border)" />
                <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(h: number) => `${h} sessions after`}
                         formatter={(v: number | number[], name: string) => (Array.isArray(v) ? [`${v[0].toFixed(2)}% to ${v[1].toFixed(2)}%`, "90% band"] : [`${v.toFixed(2)}%`, name === "b" ? "Cumulative response" : name])} />
                <Area dataKey="band" stroke="none" fill="var(--color-accent)" fillOpacity={0.12} isAnimationActive={false} />
                <Line dataKey="b" stroke="var(--color-accent)" strokeWidth={2} dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-1 text-[11px] text-muted">
            Cumulative portfolio response to a one-standard-deviation structural shock to {r.label} ({fmtUnit(r.shock, r.unit, 2)}),
            Jordà local projections with Newey-West bands, {r.n} sessions. A band that settles away from zero is a lasting effect; one that
            drifts back means the shock is absorbed.
          </p>
          <div className="num mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
            {r.assets.map((a) => (
              <span key={a.asset}>
                {a.asset} {fmtFrac(a.beta[0], true, 2)} day 0 → {fmtFrac(a.beta[a.beta.length - 1], true, 2)} day {r.horizon}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CausalSection() {
  const q = useCausalGraph();
  const g = data(q);
  const label = g ? Object.fromEntries(g.nodes.map((n) => [n.key, n.label])) : {};
  return (
    <Section
      title="Causal view of macro risk"
      note="A prior graph of how macro shocks propagate (rates and oil → dollar and credit → VIX → stocks → USD/CAD), fitted as a structural model, then checked against the data with PCMCI-style conditional independence tests."
    >
      {q.isLoading ? <Loading what="the causal model (bootstrapping)" /> : q.isError ? <LoadError error={q.error} /> : g && (
        <>
          <div className="grid grid-cols-5 border-b border-border">
            <div className="col-span-3 border-r border-border p-2">
              <Dag g={g} />
              <div className="flex flex-wrap gap-x-4 px-2 text-[11px] text-muted">
                <span><i className="mr-1 inline-block h-0.5 w-4 bg-accent align-middle" />raises the target</span>
                <span><i className="mr-1 inline-block h-0.5 w-4 bg-[#175cd3] align-middle" />lowers it</span>
                <span>solid: confirmed by the data · dashed: not found · dotted: found, not in the prior</span>
              </div>
            </div>
            <div className="col-span-2 p-3 text-[12px]">
              <div className="mb-1 font-medium">What the data say about the graph</div>
              <p className="text-muted">
                {g.discovery.confirmed.length} of {g.discovery.confirmed.length + g.discovery.not_found.length} assumed links show up as
                conditional dependence at α = {g.discovery.alpha}. {g.discovery.n} sessions, {g.start} to {g.end}.
              </p>
              {g.discovery.not_found.length > 0 && (
                <p className="mt-1.5 text-muted">
                  Not found: {g.discovery.not_found.slice(0, 6).map(([a, b]) => `${label[a]} and ${label[b]}`).join(", ")}
                  {g.discovery.not_found.length > 6 && ` and ${g.discovery.not_found.length - 6} more`}. These run through other drivers or are
                  too weak to detect.
                </p>
              )}
              {g.discovery.unexpected.length > 0 && (
                <p className="mt-1.5 text-muted">Found, not assumed: {g.discovery.unexpected.map(([a, b]) => `${label[a]} and ${label[b]}`).join(", ")}.</p>
              )}
              {g.discovery.lagged.length > 0 && (
                <p className="mt-1.5 text-muted">
                  Lead-lag links: {g.discovery.lagged.slice(0, 6).map((l) => `${label[l.from]} → ${label[l.to]} (${l.lag}d)`).join(", ")}.
                </p>
              )}
              <p className="mt-1.5 text-muted">The structural model explains {fmtFrac(g.r2, false, 0)} of the book's daily variance.</p>
            </div>
          </div>
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
                <th className="px-2 py-1.5 text-left font-medium">Driver</th>
                <th className={th} title="One standard deviation of its daily move">Typical day</th>
                <th className={th} title="do(driver = typical move): the whole effect on the book">Causal impact</th>
                <th className={th}>Direct</th>
                <th className={th} title="Through the other drivers it moves: the incremental knock-on">Knock-on</th>
                <th className={th}>90% CI</th>
                <th className={th} title="Regression of the book on this driver alone: includes confounders">Correlational</th>
              </tr>
            </thead>
            <tbody>
              {g.impacts.map((i) => (
                <tr key={i.key} className="border-b border-border/50">
                  <td className="px-2 py-1.5">{label[i.key]}</td>
                  <td className={`${td} text-muted`}>{fmtUnit(i.shock, i.unit, 2)}</td>
                  <td className={`${td} font-medium ${pnlClass(i.portfolio)}`}>{fmtFrac(i.portfolio, true)}</td>
                  <td className={`${td} ${pnlClass(i.direct)}`}>{fmtFrac(i.direct, true)}</td>
                  <td className={`${td} ${pnlClass(i.indirect)}`}>{fmtFrac(i.indirect, true)}</td>
                  <td className={`${td} text-muted ${i.ci90 && i.ci90[0] < 0 && i.ci90[1] > 0 ? "opacity-60" : ""}`}>
                    {i.ci90 ? `${fmtFrac(i.ci90[0], true)} to ${fmtFrac(i.ci90[1], true)}` : "—"}
                  </td>
                  <td className={`${td} text-muted`}>{fmtFrac(i.correlational, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-border">
            <Irf drivers={g.nodes.filter((n) => n.key !== "portfolio")} />
          </div>
        </>
      )}
    </Section>
  );
}

export function Risk() {
  const summary = useRiskSummary();
  const s = data(summary);
  if (summary.data?.empty) {
    return (
      <div className="card p-6 text-[13px]">
        <p>No holdings yet.</p>
        <p className="mt-1 text-muted">
          Import a Wealthsimple activities CSV in <Link to="/settings" className="text-accent underline">Settings</Link>.
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <p className="num text-[12px] text-muted">
          Current holdings in CAD, measured on adjusted daily closes from Yahoo{s ? ` through ${s.as_of}` : ""}. Every estimate has
          sampling error, so read it with its band and the short-history markers.
        </p>
      </div>
      {summary.isLoading ? <Loading what="risk (first run fetches price history back to 2008)" /> : summary.isError ? <LoadError error={summary.error} /> : s && <RiskRow r={s} />}
      <Decomposition />
      <Factors />
      <VarSection />
      <StressSection />
      <CausalSection />
    </div>
  );
}
