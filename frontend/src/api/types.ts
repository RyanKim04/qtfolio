export type Currency = "CAD" | "USD";

export interface TokenStatus {
  status: "absent" | "ok" | "broken";
  api_server: string | null;
  access_expires_at: string | null;
  updated_at: string | null;
}

export interface JobLastRun {
  started_at: string;
  finished_at: string | null;
  status: "running" | "success" | "error";
  detail: string | null;
}

export interface JobInfo {
  name: string;
  cron: Record<string, number | string>;
  last_run: JobLastRun | null;
}

export interface ReconWarning {
  date: string | null;
  ticker: string | null;
  message: string;
}

export interface WsImportInfo {
  at: string;
  through: string | null;
  imported: number;
}

export interface Health {
  token: TokenStatus;
  questrade: { retired: { at: string; closing_snapshot: string | null } | null };
  wealthsimple: { accounts: number; last_import: WsImportInfo | null };
  jobs: JobInfo[];
  progress: Record<string, string>;
  fx: { date: string; usdcad: number } | null;
  candle_adjustment_regime: string | null;
  series: { start: string; end: string; days: number } | null;
  warnings: ReconWarning[];
  onboarding: { has_token: boolean; token_ok: boolean; has_accounts: boolean; has_history: boolean };
}

export interface Summary {
  empty: boolean;
  currency: Currency;
  as_of: string;
  total_value?: number;
  day_pnl?: number;
  day_pnl_pct?: number | null;
  extended_hours?: boolean;
  prices_as_of?: string | null; // trade time behind the newest price
  price_session?: "post" | "pre" | null;
  quotes_live?: boolean; // inside the US pre-market → after-hours window
  open_pnl?: number;
  cash?: number;
  mwr?: number | null;
  mwr_since?: string | null;
  twr_ytd?: number | null;
  freshness?: { ledger_accounts: number; activity_through: string | null; last_import_at: string | null };
}

export type RangeKey = "1m" | "3m" | "6m" | "1y" | "all";

export interface PerfStats {
  twr: number | null;
  mwr: number | null;
  mwr_since?: string;
  max_drawdown: number | null;
  vol: number | null;
  best_day: StatDay | null;
  worst_day: StatDay | null;
}

export interface StatDay {
  date: string;
  prev: string; // previous market session — the day's return is measured from its close
  r: number;
}

export interface PerfValueSeries {
  empty: boolean;
  reason?: string;
  range: RangeKey;
  start: string;
  end: string;
  stats: PerfStats;
  dates: string[];
  value: number[];
  capital_in: number[];
  pnl: number[];
  fx_pnl: number;
  investment_pnl: number;
  flows: { date: string; amount: number; currency: string; kind: string }[];
  opening_value: number;
}

export interface PerfIndexedSeries {
  empty: boolean;
  reason?: string;
  stats: PerfStats;
  dates: string[];
  portfolio: number[];
}

export interface PerfStackedSeries {
  empty: boolean;
  reason?: string;
  stats: PerfStats;
  dates: string[];
  series: { ticker: string; values: number[] }[];
}

export interface AttributionRow {
  ticker: string;
  mv_start: number;
  mv_end: number;
  invested_net: number;
  dividends: number;
  pnl: number;
}

export interface Attribution {
  empty: boolean;
  range: RangeKey;
  start: string;
  end: string;
  live: boolean;
  rows: AttributionRow[];
  interest_fees: number;
  other: number;
  total_pnl: number;
  fx_pnl: number;
  investment_pnl: number;
}

export interface DayBreakdownRow extends AttributionRow {
  currency: string;
  qty_start: number;
  qty_end: number;
  price_start: number | null; // native
  price_end: number | null;
}

/** One session's breakdown (GET /performance/day) — the Best/Worst day cards. */
export interface DayBreakdown {
  date: string;
  prev: string;
  r: number;
  live: boolean;
  rows: DayBreakdownRow[];
  trades: { date: string; ticker: string; action: string; qty: number; price: number; currency: string }[];
  interest_fees: number;
  other: number;
  total_pnl: number;
  fx_pnl: number;
  opening_value: number;
  closing_value: number;
  flows: number;
  market: { symbol: string; r: number }[];
  usdcad: { start: number; end: number } | null;
}

export interface StockCandlesPayload {
  ticker: string;
  description: string | null;
  currency: string | null;
  interval: string;
  intraday: boolean;
  default_benchmark: string;
  candles: { t: string | number; o: number; h: number; l: number; c: number; v: number }[];
  indicators: {
    ema20: (number | null)[];
    ema50: (number | null)[];
    ema200: (number | null)[];
    bb: { upper: (number | null)[]; mid: (number | null)[]; lower: (number | null)[] };
    rsi: (number | null)[];
    macd: { macd: (number | null)[]; signal: (number | null)[]; hist: (number | null)[] };
    vwap?: (number | null)[];
    avwap_52wlow?: (number | null)[];
  };
  crosses: { t: string | number; kind: "golden" | "death" }[];
}

export interface StockLevels {
  ticker: string;
  currency: string | null;
  last: number;
  as_of: string;
  week52_high: number | null;
  week52_low: number | null;
  dist_from_high_pct: number | null;
  dist_from_low_pct: number | null;
  atr14: number | null;
  atr14_pct: number | null;
  empty?: boolean;
}

export interface StockPosition {
  ticker: string;
  owned: boolean;
  qty: number;
  avg_cost: number | null;
  market_value: number;
  currency: string | null;
  weight: number | null;
  open_pnl: number | null;
  open_pnl_pct: number | null;
  day_pnl: number | null;
  markers: { date: string; action: "buy" | "sell"; qty: number; price: number }[];
}

export interface RsSeries {
  empty: boolean;
  ticker?: string;
  benchmark?: string;
  dates?: string[];
  values?: number[];
}

export interface CompareResult {
  empty: boolean;
  dates: string[];
  series: { symbol: string; indexed: number[] | null; error?: string }[];
}

export interface ImportPreview {
  phase: "preview";
  source: "questrade" | "wealthsimple";
  n_rows: number;
  by_type: Record<string, number>;
  date_range: [string, string] | null;
  accounts: string[];
  header_map: Record<string, string>;
  unmapped_headers: string[];
  errors: string[];
  sample: Record<string, unknown>[];
}

export interface ImportResult {
  phase: "committed";
  imported: number;
  duplicates: number;
  errors: string[];
  recompute: { ok: boolean; days?: number; warnings?: number; reason?: string };
}

export interface HoldingRow {
  ticker: string;
  currency: Currency;
  qty: number;
  avg_cost: number | null;
  last: number | null;
  session: "post" | "pre" | null;
  price_time: string | null;
  market_value: number;
  weight: number | null;
  day_pnl: number | null;
  open_pnl: number;
  open_pnl_pct: number | null;
  sparkline: number[];
}

export interface Holdings {
  empty: boolean;
  currency: Currency;
  as_of: string;
  total_equity?: number;
  cash_native?: { CAD: number; USD: number };
  cash_weight?: number | null;
  rows: HoldingRow[];
}

export interface Allocation {
  by: string;
  available: boolean;
  currency?: Currency;
  slices: { label: string; value: number; pct: number }[];
}

export interface Account {
  qt_account_id: string;
  type: string | null;
  status: string | null;
  tracked: boolean;
  broker: "questrade" | "wealthsimple";
}

export interface SettingsData {
  settings: Record<string, unknown>;
  accounts: Account[];
}

export interface TestResult {
  ok: boolean;
  latency_ms?: number;
  accounts?: Account[];
  error?: string;
}

export type TfsaTxnType = "contribution" | "withdrawal" | "transfer";

export interface TfsaTxn {
  id: number;
  date: string;
  institution: string;
  type: TfsaTxnType;
  amount: number;
  note: string | null;
}

export interface TfsaConfig {
  limits: Record<string, number | null>; // null = not announced yet
  anchor: { year: number; room: number } | null; // CRA-confirmed Jan 1 room
}

export interface TfsaSummary extends TfsaConfig {
  today: string;
  configured: boolean;
  transactions: TfsaTxn[];
  year?: number;
  jan1?: number;
  contributed?: number;
  withdrawn?: number;
  remaining?: number;
  next_year?: number;
  next_limit?: number;
  next_limit_provisional?: boolean;
  projected_next_jan1?: number;
  overcontribution?: { excess: number; monthly_tax: number; since: string | null } | null;
  reconcile?: { year: number; computed_jan1: number } | null;
}

export interface TfsaWarning {
  level: "danger" | "warning" | "info";
  code: string;
  message: string;
}

export interface TfsaTxnInput {
  date: string;
  institution: string;
  type: TfsaTxnType;
  amount: string;
  note?: string;
}

export interface TfsaRecordResult {
  phase: "preview" | "committed";
  id?: number;
  year: number;
  room_after: number | null;
  warnings: TfsaWarning[];
}
