import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAttribution, useCompare, useHealth, useHoldings, usePerfIndexed, usePerfStacked, usePerfValue, useRunJob, useSettingsData } from "../api/hooks";
import type { PerfStats, PerfValueSeries, RangeKey, StatDay } from "../api/types";
import { DayBreakdownPopover } from "../components/DayBreakdown";
import { COLORS, TOOLTIP_STYLE } from "../lib/palette";
import { fmtMoney, fmtPct, fmtSignedMoney, pnlClass } from "../lib/format";

const RANGES: RangeKey[] = ["1m", "3m", "6m", "1y", "all"];
const MODES = [
  { key: "value", label: "Value" },
  { key: "compare", label: "Compare" },
  { key: "structure", label: "Structure" },
] as const;
type Mode = (typeof MODES)[number]["key"];

// Series dates are UTC-midnight timestamps. Format them in UTC, or every
// label and tooltip shifts one day back in Toronto time.
const fmtFullDate = (ts: number) =>
  new Date(ts).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });

// Explicit, non-repeating axis ticks: short ranges get ~8 day-level ticks
// ("Jul 14"); long ranges get one tick per month boundary, thinned to ≤ 12,
// with the year shown only on January ("Jan 2026").
function makeTimeAxis(dates: string[]): { ticks: number[]; fmt: (ts: number) => string } {
  if (!dates.length) return { ticks: [], fmt: () => "" };
  const start = new Date(dates[0]).getTime();
  const end = new Date(dates[dates.length - 1]).getTime();
  const spanDays = (end - start) / 86_400_000;
  if (spanDays <= 130) {
    const step = Math.max(1, Math.ceil(dates.length / 8));
    const ticks = dates.filter((_, i) => i % step === 0).map((d) => new Date(d).getTime());
    return {
      ticks,
      fmt: (ts) => new Date(ts).toLocaleDateString("en-CA", { month: "short", day: "numeric", timeZone: "UTC" }),
    };
  }
  const months: number[] = [];
  const d0 = new Date(start);
  let cur = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth() + 1, 1);
  while (cur <= end) {
    months.push(cur);
    const c = new Date(cur);
    cur = Date.UTC(c.getUTCFullYear(), c.getUTCMonth() + 1, 1);
  }
  // Every month up to 3 years, then calendar-aligned steps (quarters, then
  // Januaries), so ticks never skip in an arbitrary "Jun, Sep" pattern.
  let ticks = months;
  if (months.length > 36) {
    const quarters = months.filter((ts) => new Date(ts).getUTCMonth() % 3 === 0);
    ticks = quarters.length > 40 ? months.filter((ts) => new Date(ts).getUTCMonth() === 0) : quarters;
  }
  return {
    ticks,
    fmt: (ts) => {
      const d = new Date(ts);
      return d.getUTCMonth() === 0
        ? d.toLocaleDateString("en-CA", { month: "short", year: "numeric", timeZone: "UTC" })
        : d.toLocaleDateString("en-CA", { month: "short", timeZone: "UTC" });
    },
  };
}

function Seg<T extends string>({ value, options, onChange }: { value: T; options: readonly { key: T; label: string }[] | T[]; onChange: (v: T) => void }) {
  const opts = options.map((o) => (typeof o === "string" ? { key: o, label: o.toUpperCase() } : o));
  return (
    <div className="flex overflow-hidden rounded-md border border-border">
      {opts.map((o) => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          aria-pressed={value === o.key}
          className={`px-2.5 py-1 text-[12px] ${value === o.key ? "bg-accent font-medium text-white" : "bg-panel text-muted hover:text-text"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// Value vs capital-in, with the gap shaded green
// above / red below, with interpolated crossover points so the fill never gaps.
function ValueChart({ data, baseline }: { data: PerfValueSeries; baseline: "range" | "inception" }) {
  const rows = useMemo(() => {
    type Row = {
      ts: number; value: number | null; capital: number | null;
      up: [number, number] | null; down: [number, number] | null; flow?: { amount: number; kind: string };
    };
    const out: Row[] = [];
    const flowsByDate = new Map(data.flows.map((f) => [f.date, f]));
    const pt = (ts: number, v: number, c: number, flow?: { amount: number; kind: string }): Row => ({
      ts, value: v, capital: c,
      up: v >= c ? [c, v] : null,
      down: v < c ? [v, c] : null,
      flow,
    });
    for (let i = 0; i < data.dates.length; i++) {
      const ts = new Date(data.dates[i]).getTime();
      const v = data.value[i];
      const c = data.capital_in[i];
      if (i > 0) {
        const pv = data.value[i - 1];
        const pc = data.capital_in[i - 1];
        const prevDiff = pv - pc;
        const diff = v - c;
        if (prevDiff * diff < 0) {
          // sign change: insert the crossover so green/red areas meet cleanly
          const t = prevDiff / (prevDiff - diff);
          const pts = new Date(data.dates[i - 1]).getTime();
          const xts = pts + t * (ts - pts);
          const xv = pv + t * (v - pv);
          const xc = pc + t * (c - pc);
          out.push({ ts: xts, value: xv, capital: xc, up: [xc, xv], down: [xv, xc] });
        }
      }
      out.push(pt(ts, v, c, flowsByDate.get(data.dates[i])));
    }
    return out;
  }, [data]);

  // Flow markers use the same data array (flowY is set only on flow rows). A
  // separate Scatter data array desynchronizes Recharts' shared tooltip index.
  const minY = Math.min(...data.value, ...data.capital_in);
  const plotted = rows.map((r) => (r.flow ? { ...r, flowY: minY } : r));
  const axis = makeTimeAxis(data.dates);

  return (
    <div className="flex h-full flex-col">
      <div className="mb-1 flex items-center gap-4 px-1 text-[11px] text-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-5 rounded" style={{ background: "var(--color-text)" }} />
          Portfolio value
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="20" height="2"><line x1="0" y1="1" x2="20" y2="1" stroke="var(--color-capital)" strokeWidth="2" strokeDasharray="4 3" /></svg>
          {baseline === "inception"
            ? "Capital in = net deposits since inception"
            : `Capital in = value on ${data.start} + deposits − withdrawals since`}
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="10" height="8"><path d="M 5 0 L 9.5 8 L 0.5 8 Z" fill="var(--color-accent)" /></svg>
          Deposit / withdrawal
        </span>
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={plotted} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <XAxis
          dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]}
          ticks={axis.ticks} tickFormatter={axis.fmt} tick={{ fontSize: 11, fill: "var(--color-muted)" }}
          axisLine={{ stroke: "var(--color-border)" }} tickLine={false}
        />
        <YAxis
          domain={["auto", "auto"]} width={70}
          tickFormatter={(v: number) => fmtMoney(v, "CAD", 0)}
          tick={{ fontSize: 11, fill: "var(--color-muted)" }}
          axisLine={false} tickLine={false}
        />
        <Area dataKey="up" stroke="none" fill="var(--color-up)" fillOpacity={0.13} isAnimationActive={false} connectNulls={false} />
        <Area dataKey="down" stroke="none" fill="var(--color-down)" fillOpacity={0.13} isAnimationActive={false} connectNulls={false} />
        <Line dataKey="value" stroke="var(--color-text)" strokeWidth={1.6} dot={false} isAnimationActive={false} />
        <Line dataKey="capital" stroke="var(--color-capital)" strokeWidth={1.2} strokeDasharray="5 3" dot={false} isAnimationActive={false} />
        <Scatter dataKey="flowY" shape={(p: any) =>
          // Recharts calls the shape for every row, including rows where flowY
          // is undefined (drawn stuck to the chart top), so draw only on
          // deposit and withdrawal rows.
          p.payload.flow && Number.isFinite(p.cy) ? (
            <path
              d={`M ${p.cx} ${p.cy - 5} L ${p.cx + 4.5} ${p.cy + 3} L ${p.cx - 4.5} ${p.cy + 3} Z`}
              fill={p.payload.flow.kind === "deposit" ? "var(--color-accent)" : "var(--color-muted)"}
            />
          ) : (
            <g />
          )
        } isAnimationActive={false} />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelFormatter={(ts: number) => fmtFullDate(ts)}
          formatter={() => []}
          content={({ active, payload, label }) => {
            if (!active || !payload?.length) return null;
            // Read the row through the value series. Payload order isn't stable.
            const r = payload.find((p) => p.dataKey === "value")?.payload ?? payload[0].payload;
            if (r.value == null) return null;
            const pnl = r.value - r.capital;
            return (
              <div style={TOOLTIP_STYLE} className="px-2.5 py-2">
                <div className="text-muted">{fmtFullDate(label as number)}</div>
                <div className="num">portfolio value {fmtMoney(r.value, "CAD")}</div>
                <div className="num text-muted">capital in {fmtMoney(r.capital, "CAD")}</div>
                <div className={`num ${pnlClass(pnl)}`}>
                  P&L {baseline === "inception" ? "(lifetime)" : `since ${data.start}`} {fmtSignedMoney(pnl, "CAD")}
                </div>
                {r.flow && (
                  <div className="num text-accent">
                    {r.flow.kind} {fmtMoney(Math.abs(r.flow.amount), "CAD")}
                  </div>
                )}
              </div>
            );
          }}
        />
      </ComposedChart>
    </ResponsiveContainer>
      </div>
    </div>
  );
}

function CompareChart({ range, logScale }: { range: RangeKey; logScale: boolean }) {
  const indexed = usePerfIndexed(range, true);
  const holdings = useHoldings("CAD");
  const settings = useSettingsData();
  const benchmarks: string[] = (settings.data?.settings.benchmarks as string[]) ?? ["QQQ", "SMH", "SPY", "XEQT.TO"];
  const owned = holdings.data?.rows.map((r) => r.ticker) ?? [];
  const [selected, setSelected] = useState<string[]>(benchmarks.slice(0, 1));
  const compare = useCompare(selected, range);

  const rows = useMemo(() => {
    if (!indexed.data || indexed.data.empty) return [];
    const overlayByDate: Record<string, Record<string, number>> = {};
    if (compare.data && !compare.data.empty) {
      compare.data.series.forEach((s) => {
        s.indexed?.forEach((v, i) => {
          const d = compare.data!.dates[i];
          (overlayByDate[d] ??= {})[s.symbol] = v;
        });
      });
    }
    return indexed.data.dates.map((d, i) => ({
      ts: new Date(d).getTime(),
      Portfolio: indexed.data!.portfolio[i],
      ...overlayByDate[d],
    }));
  }, [indexed.data, compare.data]);

  const toggle = (s: string) =>
    setSelected((cur) => (cur.includes(s) ? cur.filter((x) => x !== s) : [...cur, s].slice(-8)));

  const failed = compare.data?.series.filter((s) => s.error) ?? [];
  const axis = makeTimeAxis(indexed.data?.empty === false ? indexed.data.dates : []);
  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex flex-wrap gap-1.5">
        {[...benchmarks, ...owned.filter((o) => !benchmarks.includes(o))].map((s) => (
          <button
            key={s}
            onClick={() => toggle(s)}
            className={`num rounded-full border px-2 py-0.5 text-[11px] ${
              selected.includes(s) ? "border-accent bg-accent-soft text-accent" : "border-border text-muted hover:text-text"
            }`}
          >
            {s}
          </button>
        ))}
        {failed.length > 0 && (
          <span className="ml-1 self-center text-[11px] text-muted">
            No data for {failed.map((f) => f.symbol).join(", ")}
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
            <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} ticks={axis.ticks} tickFormatter={axis.fmt}
                   tick={{ fontSize: 11, fill: "var(--color-muted)" }} axisLine={{ stroke: "var(--color-border)" }} tickLine={false} />
            <YAxis scale={logScale ? "log" : "linear"} domain={["auto", "auto"]} width={52}
                   tickFormatter={(v: number) => v.toFixed(0)}
                   tick={{ fontSize: 11, fill: "var(--color-muted)" }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(ts: number) => fmtFullDate(ts)}
                     formatter={(v: number) => v.toFixed(1)} />
            <Line dataKey="Portfolio" stroke="var(--color-accent)" strokeWidth={2} dot={false} isAnimationActive={false} />
            {selected.map((s, i) => (
              <Line key={s} dataKey={s} stroke={COLORS[(i + 1) % COLORS.length]} strokeWidth={1.2} dot={false} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-[11px] text-muted">
        Indexed to 100 at range start. Portfolio is the time-weighted return, which leaves out deposits. Other lines
        use adjusted closes.
      </p>
    </div>
  );
}

function StructureChart({ range }: { range: RangeKey }) {
  const stacked = usePerfStacked(range, true);
  const rows = useMemo(() => {
    if (!stacked.data || stacked.data.empty) return [];
    return stacked.data.dates.map((d, i) => {
      const row: Record<string, number> = { ts: new Date(d).getTime() };
      stacked.data!.series.forEach((s) => (row[s.ticker] = s.values[i]));
      return row;
    });
  }, [stacked.data]);
  if (stacked.isLoading) return <p className="py-16 text-center text-[13px] text-muted">Loading…</p>;
  const names = stacked.data?.series.map((s) => s.ticker) ?? [];
  const axis = makeTimeAxis(stacked.data?.empty === false ? stacked.data.dates : []);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
        <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} ticks={axis.ticks} tickFormatter={axis.fmt}
               tick={{ fontSize: 11, fill: "var(--color-muted)" }} axisLine={{ stroke: "var(--color-border)" }} tickLine={false} />
        <YAxis width={70} tickFormatter={(v: number) => fmtMoney(v, "CAD", 0)}
               tick={{ fontSize: 11, fill: "var(--color-muted)" }} axisLine={false} tickLine={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} labelFormatter={(ts: number) => fmtFullDate(ts)}
                 formatter={(v: number, name: string) => [fmtMoney(v, "CAD"), name]} />
        {names.map((t, i) => (
          <Area key={t} dataKey={t} stackId="1" stroke="none" isAnimationActive={false}
                fill={t === "Cash" ? "#c9d2dc" : COLORS[i % COLORS.length]} fillOpacity={0.85} />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}

function StatsStrip({ stats }: { stats: PerfStats }) {
  const items: { label: string; value: string; cls?: string; day?: StatDay }[] = [
    { label: "TWR", value: fmtPct(stats.twr != null ? stats.twr * 100 : null), cls: pnlClass(stats.twr) },
    {
      label: stats.mwr_since ? `MWR since ${stats.mwr_since}` : "MWR",
      value: stats.mwr != null ? fmtPct(stats.mwr * 100) : "n/a",
      cls: pnlClass(stats.mwr),
    },
    { label: "Max drawdown", value: fmtPct(stats.max_drawdown != null ? stats.max_drawdown * 100 : null, false) },
    { label: "Volatility (ann.)", value: fmtPct(stats.vol != null ? stats.vol * 100 : null, false) },
    {
      label: `Best day${stats.best_day ? ` · ${stats.best_day.date}` : ""}`,
      value: fmtPct(stats.best_day ? stats.best_day.r * 100 : null), cls: "text-up",
      day: stats.best_day ?? undefined,
    },
    {
      label: `Worst day${stats.worst_day ? ` · ${stats.worst_day.date}` : ""}`,
      value: fmtPct(stats.worst_day ? stats.worst_day.r * 100 : null), cls: "text-down",
      day: stats.worst_day ?? undefined,
    },
  ];
  return (
    <div className="grid grid-cols-6 gap-2">
      {items.map((it) => (it.day ? <DayStat key={it.label} {...it} day={it.day} /> : (
        <div key={it.label} className="card px-3 py-2">
          <div className="truncate text-[11px] font-medium uppercase tracking-wide text-muted" title={it.label}>{it.label}</div>
          <div className={`num mt-0.5 text-[15px] font-semibold ${it.cls ?? ""}`}>{it.value}</div>
        </div>
      )))}
    </div>
  );
}

/** A Best/Worst day card: hover (or focus) opens that session's breakdown.
 *  The popover sits inside the hover target so the pointer can move into it. */
function DayStat({ label, value, cls, day }: { label: string; value: string; cls?: string; day: StatDay }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="relative"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setOpen(false)}
    >
      <div tabIndex={0} className="card cursor-default px-3 py-2 outline-none hover:border-accent focus-visible:border-accent">
        <div className="truncate text-[11px] font-medium uppercase tracking-wide text-muted underline decoration-dotted underline-offset-2" title={label}>{label}</div>
        <div className={`num mt-0.5 text-[15px] font-semibold ${cls ?? ""}`}>{value}</div>
      </div>
      {open && (
        <div className="absolute right-0 bottom-full z-30 pb-1">
          <DayBreakdownPopover day={day} />
        </div>
      )}
    </div>
  );
}

function AttributionTable({ range }: { range: RangeKey }) {
  const attr = useAttribution(range);
  if (attr.isLoading || !attr.data || attr.data.empty) return null;
  const a = attr.data;
  // A window that reaches inception starts empty, so the start column only shows mid-history.
  const showStart = a.rows.some((r) => r.mv_start !== 0);
  const cols = showStart ? 6 : 5;
  const th = "px-2 py-1.5 text-right font-medium";
  const td = "num px-2 py-1.5 text-right";
  const money = (v: number) => (v ? fmtMoney(v, "CAD") : "—");
  const amt = (v: number) => <span className={`num ${pnlClass(v)}`}>{fmtSignedMoney(v, "CAD")}</span>;
  const holding = a.rows.filter((r) => r.qty_end > 0);
  const closed = a.rows.filter((r) => r.qty_end <= 0);
  const sum = (rows: typeof a.rows, k: "mv_start" | "bought" | "sold" | "mv_end" | "pnl") =>
    rows.reduce((t, r) => t + r[k], 0);
  const divTip = a.rows
    .filter((r) => r.dividends)
    .sort((x, y) => y.dividends - x.dividends)
    .map((r) => `${r.ticker} ${fmtMoney(r.dividends, "CAD")}`)
    .join(", ");

  const group = (label: string, rows: typeof a.rows) =>
    rows.length > 0 && (
      <>
        <tr className="border-b border-border bg-bg text-[11px] uppercase tracking-wide text-muted">
          <td className="px-2 py-1 font-medium" colSpan={cols}>{label}</td>
        </tr>
        {rows.map((r) => (
          <tr key={r.ticker} className="border-b border-border/50">
            <td className="num px-2 py-1.5 font-medium">
              <Link to={`/stocks/${encodeURIComponent(r.ticker)}`} className="hover:text-accent">{r.ticker}</Link>
            </td>
            {showStart && <td className={`${td} text-muted`}>{money(r.mv_start)}</td>}
            <td className={`${td} text-muted`}>{money(r.bought)}</td>
            <td className={`${td} text-muted`}>{money(r.sold)}</td>
            <td className={td}>{money(r.mv_end)}</td>
            <td className={`${td} font-medium ${pnlClass(r.pnl)}`}>{fmtSignedMoney(r.pnl, "CAD")}</td>
          </tr>
        ))}
        <tr className="border-b border-border text-muted">
          <td className="px-2 py-1.5">{label} total</td>
          {showStart && <td className={td}>{money(sum(rows, "mv_start"))}</td>}
          <td className={td}>{money(sum(rows, "bought"))}</td>
          <td className={td}>{money(sum(rows, "sold"))}</td>
          <td className={td}>{money(sum(rows, "mv_end"))}</td>
          <td className={`${td} font-medium ${pnlClass(sum(rows, "pnl"))}`}>{fmtSignedMoney(sum(rows, "pnl"), "CAD")}</td>
        </tr>
      </>
    );

  return (
    <section className="card overflow-hidden">
      <div className="flex items-baseline justify-between border-b border-border px-3 py-2">
        <div>
          <h2 className="text-[13px] font-medium">P&L by position</h2>
          <div className="text-[11px] text-muted">
            Position P&L = value now + sold − bought{showStart && ` − value on ${a.start}`}. Dividends, currency and
            fees are separate lines below.
          </div>
        </div>
        <span className="num text-[11px] text-muted">
          {a.start} → {a.end} · CAD{a.live && <span className="ml-1 text-up">· live</span>}
        </span>
      </div>
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-border text-[11px] uppercase tracking-wide text-muted">
            <th className="px-2 py-1.5 text-left font-medium">Ticker</th>
            {showStart && <th className={th} title="What you already held when this range began">Value on {a.start}</th>}
            <th className={th} title="Cash spent buying in this range">Bought</th>
            <th className={th} title="Cash received from selling in this range">Sold</th>
            <th className={th}>Value now</th>
            <th className={th}>P&L</th>
          </tr>
        </thead>
        <tbody>
          {group("Holding", holding)}
          {group("Closed", closed)}
          <tr className="border-b border-border bg-bg text-[11px] uppercase tracking-wide text-muted">
            <td className="px-2 py-1 font-medium" colSpan={cols}>Outside positions</td>
          </tr>
          {/* DECISION: fees, rebates and the unexplained remainder share one line. Rebates
              mostly cancel fees, and the remainder is small once FX has its own lines. */}
          {([
            ["Dividends", a.dividends, `Cash dividends received, after tax withheld at source. ${divTip}`],
            ["Interest", a.interest, "Interest paid on cash"],
            ["USD cash FX", a.usd_cash_fx, "What USD/CAD moves did to USD cash you held between trades"],
            ["Currency conversion", a.conversion_cost, "What converting between CAD and USD cost versus the market rate (the broker's spread)"],
            [
              "Fees and adjustments", a.adjustments,
              `Fees and taxes ${fmtSignedMoney(a.fees, "CAD")}, fee rebates ${fmtSignedMoney(a.rebates, "CAD")}, ` +
                `data adjustments ${fmtSignedMoney(a.other, "CAD")} (opening balance gap, snapshot seams, ` +
                "rounding, trades not yet imported).",
            ],
          ] as const)
            .filter(([, v]) => Math.abs(v) >= 0.01)
            .map(([label, v, tip]) => (
              <tr key={label} className="border-b border-border/50">
                <td className="px-2 py-1.5 underline decoration-border decoration-dotted underline-offset-2" title={tip}>{label}</td>
                <td className={td} colSpan={cols - 2}></td>
                <td className={`${td} font-medium ${pnlClass(v)}`}>{fmtSignedMoney(v, "CAD")}</td>
              </tr>
            ))}
          <tr className="border-t border-border">
            <td className="px-2 py-2 font-semibold">Total</td>
            <td className={td} colSpan={cols - 2}></td>
            <td className={`${td} font-semibold ${pnlClass(a.total_pnl)}`}>{fmtSignedMoney(a.total_pnl, "CAD")}</td>
          </tr>
          <tr className="border-t border-border">
            <td
              className="px-2 py-1.5 text-[11px] text-muted"
              colSpan={cols}
              title="The same total split by currency. FX is what USD/CAD moves did to your USD stocks and cash in CAD, plus what converting currencies cost. Investment is everything else."
            >
              Of which investment {amt(a.investment_pnl)}{"  ·  "}FX (USD/CAD) {amt(a.fx_pnl)}: rate moves on USD
              stocks {amt(a.fx_rate_pnl - a.usd_cash_fx)}, on USD cash {amt(a.usd_cash_fx)}, conversion cost{" "}
              {amt(a.conversion_cost)}
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}

export function Performance() {
  // Owner decision 2026-10-31: every visit opens on Value · ALL · Lifetime P&L (not remembered).
  const [range, setRange] = useState<RangeKey>("all");
  const [mode, setMode] = useState<Mode>("value");
  const [logScale, setLogScale] = useState(false);
  const [baseline, setBaseline] = useState<"range" | "inception">("inception");
  const value = usePerfValue(range, baseline);
  const health = useHealth();
  const runJob = useRunJob();
  const refreshing =
    Boolean(health.data?.progress?.eod_snapshot) ||
    health.data?.jobs.some((j) => j.name === "eod_snapshot" && j.last_run?.status === "running") ||
    (runJob.isPending && runJob.variables === "eod_snapshot");

  if (value.data?.empty) {
    return (
      <div className="card p-6 text-[13px]">
        <p>No history yet.</p>
        <p className="mt-1 text-muted">
          Import a Wealthsimple activities export in{" "}
          <Link to="/settings" className="text-accent underline">
            Settings
          </Link>
          .
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <Seg value={mode} options={MODES} onChange={setMode} />
        <Seg value={range} options={RANGES} onChange={setRange} />
        {mode === "value" && (
          <Seg
            value={baseline}
            options={[
              { key: "range" as const, label: "Window P&L" },
              { key: "inception" as const, label: "Lifetime P&L" },
            ]}
            onChange={setBaseline}
          />
        )}
        {mode === "compare" && (
          <label className="flex items-center gap-1.5 text-[12px] text-muted">
            <input type="checkbox" checked={logScale} onChange={(e) => setLogScale(e.target.checked)} className="accent-(--color-accent)" />
            log scale
          </label>
        )}
        <div className="ml-auto flex items-center gap-2.5">
          <span className="num text-[11px] text-muted" title="Daily closes. Refresh records today's value from live prices.">
            all values CAD · data through {value.data && !value.data.empty ? value.data.end : "—"}
          </span>
          <button
            onClick={() => runJob.mutate("eod_snapshot")}
            disabled={refreshing}
            className="rounded-md border border-border bg-panel px-3 py-1 text-[12px] text-text hover:bg-bg disabled:opacity-50"
          >
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      <div className="card overflow-hidden p-3" style={{ height: 420 }}>
        {value.isLoading ? (
          <p className="py-16 text-center text-[13px] text-muted">Loading…</p>
        ) : mode === "value" && value.data ? (
          <ValueChart data={value.data} baseline={baseline} />
        ) : mode === "compare" ? (
          <CompareChart range={range} logScale={logScale} />
        ) : (
          <StructureChart range={range} />
        )}
      </div>

      {value.data?.stats && <StatsStrip stats={value.data.stats} />}

      <AttributionTable range={range} />
    </div>
  );
}
