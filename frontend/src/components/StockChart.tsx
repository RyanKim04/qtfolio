import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { StockCandlesPayload, StockPosition } from "../api/types";

export interface IndicatorToggles {
  ema: boolean;
  bb: boolean;
  vwap: boolean;
  avwap: boolean;
  rsi: boolean;
  macd: boolean;
}

const C = {
  text: "#1a1f36",
  muted: "#6a7383",
  border: "#e3e8ee",
  up: "#17803d",
  down: "#df1b41",
  accent: "#635bff",
  ema20: "#ec4899",
  ema50: "#2e90fa",
  ema200: "#7c3aed",
  bb: "#98a2b3",
  vwap: "#b692f6",
  avwap: "#36bffa",
  avgCost: "#e8a33d",
  volume: "#dbe1e8",
};

function toTime(t: string | number): Time {
  return (typeof t === "number" ? (t as UTCTimestamp) : t) as Time;
}

function lineData(times: (string | number)[], values: (number | null)[]) {
  const out: { time: Time; value: number }[] = [];
  values.forEach((v, i) => {
    if (v != null) out.push({ time: toTime(times[i]), value: v });
  });
  return out;
}

export function StockChart({
  payload,
  toggles,
  position,
}: {
  payload: StockCandlesPayload;
  toggles: IndicatorToggles;
  position: StockPosition | undefined;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const height = 440 + (toggles.rsi ? 110 : 0) + (toggles.macd ? 110 : 0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "#ffffff" }, textColor: C.muted, fontSize: 11, panes: { separatorColor: C.border } },
      grid: { vertLines: { color: "#f2f5f8" }, horzLines: { color: "#f2f5f8" } },
      rightPriceScale: { borderColor: C.border },
      timeScale: { borderColor: C.border, timeVisible: payload.intraday, secondsVisible: false },
      crosshair: { horzLine: { labelBackgroundColor: C.text }, vertLine: { labelBackgroundColor: C.text } },
    });
    chartRef.current = chart;

    const times = payload.candles.map((b) => b.t);
    const timeSet = new Set(times.map(String));
    const ind = payload.indicators;

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: C.up,
      downColor: C.down,
      borderVisible: false,
      wickUpColor: C.up,
      wickDownColor: C.down,
    });
    candles.setData(payload.candles.map((b) => ({ time: toTime(b.t), open: b.o, high: b.h, low: b.l, close: b.c })));

    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      color: C.volume,
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    volume.setData(payload.candles.map((b) => ({ time: toTime(b.t), value: b.v })));

    const addLine = (values: (number | null)[] | undefined, color: string, width: 1 | 2 = 1, style = LineStyle.Solid) => {
      if (!values) return;
      const s = chart.addSeries(LineSeries, {
        color, lineWidth: width, lineStyle: style, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData(lineData(times, values));
    };

    if (toggles.ema) {
      addLine(ind.ema20, C.ema20);
      addLine(ind.ema50, C.ema50);
      addLine(ind.ema200, C.ema200, 2);
    }
    if (toggles.bb) {
      addLine(ind.bb.upper, C.bb, 1, LineStyle.Dotted);
      addLine(ind.bb.mid, C.bb, 1, LineStyle.Dashed);
      addLine(ind.bb.lower, C.bb, 1, LineStyle.Dotted);
    }
    if (toggles.vwap && ind.vwap) addLine(ind.vwap, C.vwap, 2);
    if (toggles.avwap && ind.avwap_52wlow) addLine(ind.avwap_52wlow, C.avwap, 1, LineStyle.Dashed);

    // Owner overlay: avg-cost price line labeled with qty (§8.4).
    if (position?.owned && position.avg_cost) {
      candles.createPriceLine({
        price: position.avg_cost,
        color: C.avgCost,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `avg ${position.qty.toFixed(position.qty % 1 ? 2 : 0)}`,
      });
    }

    // Markers: trades (daily only) + EMA50/200 crosses, sorted by time.
    const markers: SeriesMarker<Time>[] = [];
    if (payload.interval === "1d" && position) {
      for (const m of position.markers) {
        if (!timeSet.has(m.date)) continue;
        markers.push({
          time: toTime(m.date),
          position: m.action === "buy" ? "belowBar" : "aboveBar",
          shape: m.action === "buy" ? "arrowUp" : "arrowDown",
          color: m.action === "buy" ? C.accent : C.muted,
          text: `${m.action === "buy" ? "B" : "S"} ${m.qty}@${m.price}`,
          size: 1,
        });
      }
    }
    for (const cr of payload.crosses) {
      markers.push({
        time: toTime(cr.t),
        position: cr.kind === "golden" ? "belowBar" : "aboveBar",
        shape: "circle",
        color: cr.kind === "golden" ? C.avgCost : C.bb,
        text: cr.kind === "golden" ? "GC" : "DC",
        size: 0,
      });
    }
    markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    if (markers.length) createSeriesMarkers(candles, markers);

    // Sub-panes.
    let pane = 1;
    if (toggles.rsi) {
      const rsi = chart.addSeries(
        LineSeries,
        { color: C.accent, lineWidth: 1, priceLineVisible: false, lastValueVisible: true },
        pane,
      );
      rsi.setData(lineData(times, ind.rsi));
      for (const level of [30, 70]) {
        rsi.createPriceLine({ price: level, color: C.border, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
      }
      try {
        chart.panes()[pane]?.setHeight(110);
      } catch { /* pane sizing is cosmetic */ }
      pane += 1;
    }
    if (toggles.macd) {
      const m = ind.macd;
      const hist = chart.addSeries(
        HistogramSeries,
        { priceLineVisible: false, lastValueVisible: false, color: C.volume },
        pane,
      );
      hist.setData(
        m.hist
          .map((v, i) => (v == null ? null : { time: toTime(times[i]), value: v, color: v >= 0 ? "#17803d55" : "#df1b4155" }))
          .filter((x): x is { time: Time; value: number; color: string } => x !== null),
      );
      const line = chart.addSeries(
        LineSeries,
        { color: C.ema200, lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
        pane,
      );
      line.setData(lineData(times, m.macd));
      const signal = chart.addSeries(
        LineSeries,
        { color: C.avgCost, lineWidth: 1, priceLineVisible: false, lastValueVisible: false },
        pane,
      );
      signal.setData(lineData(times, m.signal));
      try {
        chart.panes()[pane]?.setHeight(110);
      } catch { /* pane sizing is cosmetic */ }
    }

    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [payload, toggles, position]);

  const legend: { label: string; color: string; dashed?: boolean }[] = [];
  if (toggles.ema) {
    legend.push({ label: "EMA 20", color: C.ema20 }, { label: "EMA 50", color: C.ema50 }, { label: "EMA 200", color: C.ema200 });
  }
  if (toggles.bb) legend.push({ label: "Bollinger", color: C.bb, dashed: true });
  if (toggles.vwap && payload.intraday) legend.push({ label: "VWAP", color: C.vwap });
  if (toggles.avwap && !payload.intraday) legend.push({ label: "AVWAP 52w low", color: C.avwap, dashed: true });
  if (position?.owned && position.avg_cost) legend.push({ label: "Avg cost", color: C.avgCost, dashed: true });

  return (
    <div>
      {legend.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 px-2 pb-1 text-[11px] text-muted">
          {legend.map((l) => (
            <span key={l.label} className="flex items-center gap-1.5">
              <svg width="18" height="2">
                <line x1="0" y1="1" x2="18" y2="1" stroke={l.color} strokeWidth="2" strokeDasharray={l.dashed ? "4 3" : undefined} />
              </svg>
              {l.label}
            </span>
          ))}
        </div>
      )}
      <div ref={containerRef} style={{ height }} className="w-full" />
    </div>
  );
}

export function MiniLineChart({ dates, values, color = "#635bff" }: { dates: string[]; values: number[]; color?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "#ffffff" }, textColor: "#6a7383", fontSize: 10 },
      grid: { vertLines: { visible: false }, horzLines: { color: "#f2f5f8" } },
      rightPriceScale: { borderColor: "#e3e8ee" },
      timeScale: { borderColor: "#e3e8ee" },
      handleScroll: false,
      handleScale: false,
    });
    const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false });
    s.setData(dates.map((d, i) => ({ time: d as Time, value: values[i] })));
    s.createPriceLine({ price: 100, color: "#e3e8ee", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [dates, values, color]);
  return <div ref={ref} style={{ height: 130 }} className="w-full" />;
}
