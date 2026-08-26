import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useRs, useStockCandles, useStockLevels, useStockPosition } from "../api/hooks";
import { useCurrency } from "../App";
import { MiniLineChart, StockChart, type IndicatorToggles } from "../components/StockChart";
import { fmtMoney, fmtNum, fmtPct, fmtQty, fmtSignedMoney, pnlClass } from "../lib/format";

const INTERVALS = ["5m", "15m", "1h", "1d", "1w"];
const DEFAULT_TOGGLES: IndicatorToggles = { ema: true, bb: false, vwap: false, avwap: false, rsi: false, macd: false };

function Chip({ on, label, onClick, disabled }: { on: boolean; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={disabled ? "Intraday intervals only" : undefined}
      className={`rounded-full border px-2.5 py-0.5 text-[11px] ${
        on ? "border-accent bg-accent-soft font-medium text-accent" : disabled ? "cursor-not-allowed border-border text-border" : "border-border text-muted hover:text-text"
      }`}
    >
      {label}
    </button>
  );
}

function Cell({ label, value, cls = "" }: { label: string; value: string; cls?: string }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`num mt-0.5 text-[14px] font-medium ${cls}`}>{value}</div>
    </div>
  );
}

export function StockDetail() {
  const { ticker = "" } = useParams();
  const t = ticker.toUpperCase();
  const navigate = useNavigate();
  const { currency } = useCurrency();
  const [interval, setInterval_] = useState("1d");
  const [toggles, setToggles] = useState<IndicatorToggles>(DEFAULT_TOGGLES);
  const [benchmark, setBenchmark] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const candles = useStockCandles(t, interval);
  const levels = useStockLevels(t);
  const position = useStockPosition(t);
  const bench = benchmark ?? candles.data?.default_benchmark ?? "QQQ";
  const rs = useRs(t, bench);

  const flip = (k: keyof IndicatorToggles) => setToggles((s) => ({ ...s, [k]: !s[k] }));
  const intraday = ["5m", "15m", "1h"].includes(interval);
  const cur = candles.data?.currency ?? "USD";
  const p = position.data;

  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-3">
        <h1 className="num text-lg font-semibold tracking-tight">{t}</h1>
        <span className="max-w-md truncate text-[12px] text-muted">{candles.data?.description ?? ""}</span>
        {levels.data && !levels.data.empty && (
          <span className="num text-[14px] font-medium">{fmtMoney(levels.data.last, cur)}</span>
        )}
        {p?.owned && p.day_pnl != null && (
          <span className={`num text-[12px] ${pnlClass(p.day_pnl)}`}>{fmtSignedMoney(p.day_pnl, cur)} today</span>
        )}
        <form
          className="ml-auto"
          onSubmit={(e) => {
            e.preventDefault();
            if (search.trim()) navigate(`/stocks/${search.trim().toUpperCase()}`);
            setSearch("");
          }}
        >
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Go to symbol…"
            className="num w-36 rounded-md border border-border bg-panel px-2.5 py-1 text-[12px] placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
          />
        </form>
      </div>

      <div className="flex items-center gap-3">
        <div className="flex overflow-hidden rounded-md border border-border">
          {INTERVALS.map((iv) => (
            <button
              key={iv}
              onClick={() => setInterval_(iv)}
              className={`px-2.5 py-1 text-[12px] ${interval === iv ? "bg-accent font-medium text-white" : "bg-panel text-muted hover:text-text"}`}
            >
              {iv.toUpperCase()}
            </button>
          ))}
        </div>
        <div className="flex gap-1.5">
          <Chip on={toggles.ema} label="EMA 20/50/200" onClick={() => flip("ema")} />
          <Chip on={toggles.bb} label="Bollinger" onClick={() => flip("bb")} />
          <Chip on={toggles.vwap} label="VWAP" onClick={() => flip("vwap")} disabled={!intraday} />
          <Chip on={toggles.avwap} label="AVWAP (52w low)" onClick={() => flip("avwap")} disabled={intraday} />
          <Chip on={toggles.rsi} label="RSI" onClick={() => flip("rsi")} />
          <Chip on={toggles.macd} label="MACD" onClick={() => flip("macd")} />
        </div>
      </div>

      <div className="card overflow-hidden p-2">
        {candles.isLoading ? (
          <p className="py-24 text-center text-[13px] text-muted">Loading candles…</p>
        ) : candles.isError ? (
          <p className="py-24 text-center text-[13px] text-down">
            {(candles.error as Error).message || `Could not load ${t}`}
          </p>
        ) : candles.data && candles.data.candles.length ? (
          <StockChart payload={candles.data} toggles={toggles} position={p} />
        ) : (
          <p className="py-24 text-center text-[13px] text-muted">No candle data for {t} on {interval}.</p>
        )}
      </div>

      <div className="card overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
          <h2 className="text-[12px] font-medium">
            Relative strength vs {bench} <span className="text-muted">(indexed to 100, 1y)</span>
          </h2>
          <div className="flex gap-1">
            {[candles.data?.default_benchmark ?? "QQQ", "QQQ", "SMH", "SPY"]
              .filter((b, i, a) => a.indexOf(b) === i)
              .map((b) => (
                <Chip key={b} on={bench === b} label={b} onClick={() => setBenchmark(b)} />
              ))}
          </div>
        </div>
        <div className="p-2">
          {rs.data && !rs.data.empty ? (
            <MiniLineChart dates={rs.data.dates!} values={rs.data.values!} />
          ) : (
            <p className="py-8 text-center text-[12px] text-muted">RS data unavailable.</p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <section className="card overflow-hidden">
          <h2 className="border-b border-border px-3 py-2 text-[13px] font-medium">Position</h2>
          {p?.owned ? (
            <div className="grid grid-cols-4 gap-3 p-3">
              <Cell label="Qty" value={fmtQty(p.qty)} />
              <Cell label="Avg cost" value={fmtMoney(p.avg_cost, cur)} />
              <Cell label="Market value" value={fmtMoney(p.market_value, cur)} />
              <Cell label="Weight" value={fmtPct(p.weight, false)} />
              <Cell label="Open P&L" value={fmtSignedMoney(p.open_pnl, cur)} cls={pnlClass(p.open_pnl)} />
              <Cell label="Open P&L %" value={fmtPct(p.open_pnl_pct)} cls={pnlClass(p.open_pnl_pct)} />
              <Cell label="Day P&L" value={fmtSignedMoney(p.day_pnl, cur)} cls={pnlClass(p.day_pnl)} />
              <Cell label="Trades" value={String(p.markers.length)} />
            </div>
          ) : (
            <p className="p-4 text-[13px] text-muted">Not currently held{p && p.markers.length ? ` — ${p.markers.length} past trades shown on the daily chart` : ""}.</p>
          )}
        </section>

        <section className="card overflow-hidden">
          <h2 className="border-b border-border px-3 py-2 text-[13px] font-medium">Levels</h2>
          {levels.data && !levels.data.empty ? (
            <div className="grid grid-cols-3 gap-3 p-3">
              <Cell label="Last" value={fmtMoney(levels.data.last, cur)} />
              <Cell label="52w high" value={`${fmtMoney(levels.data.week52_high, cur)} (${fmtPct(levels.data.dist_from_high_pct)})`} />
              <Cell label="52w low" value={`${fmtMoney(levels.data.week52_low, cur)} (${fmtPct(levels.data.dist_from_low_pct)})`} />
              <Cell label="ATR(14)" value={fmtNum(levels.data.atr14)} />
              <Cell label="ATR %" value={fmtPct(levels.data.atr14_pct, false)} />
              <Cell label="Currency" value={`${cur} · shown native (header ${currency})`} />
            </div>
          ) : (
            <p className="p-4 text-[13px] text-muted">Loading…</p>
          )}
        </section>
      </div>

      <p className="text-[11px] text-muted">Analyst consensus panel arrives with M5.</p>
    </div>
  );
}
