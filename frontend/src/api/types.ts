export type Currency = "CAD" | "USD";

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
  file?: string | null;
}

export interface Health {
  wealthsimple: {
    accounts: number;
    last_import: WsImportInfo | null;
    watch: { dir: string; error: string | null };
  };
  jobs: JobInfo[];
  progress: Record<string, string>;
  fx: { date: string; usdcad: number } | null;
  series: { start: string; end: string; days: number } | null;
  warnings: ReconWarning[];
  onboarding: { has_accounts: boolean; has_history: boolean };
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
  prev: string; // previous market session; the day's return is measured from its close
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
  qty_end: number;
  mv_start: number;
  mv_end: number;
  invested_net: number;
  bought: number; // cash spent on buys in the window
  sold: number; // cash received from sells in the window
  dividends: number;
  pnl: number; // price P&L: mv_end − mv_start + sold − bought
}

export interface Attribution {
  empty: boolean;
  range: RangeKey;
  start: string;
  end: string;
  live: boolean;
  rows: AttributionRow[];
  /** Window P&L by line: price_gains (Σ row pnl) + dividends + interest + usd_cash_fx
   *  + conversion_cost + adjustments = total_pnl. */
  price_gains: number;
  dividends: number;
  interest: number;
  usd_cash_fx: number; // USD/CAD moves on USD cash held
  conversion_cost: number; // CAD↔USD conversions vs the market rate (≤ 0)
  adjustments: number; // fees + rebates + other
  fees: number; // broker fees, sales tax, withholding tax (≤ 0)
  rebates: number;
  other: number; // opening balance gap, snapshot seams, rounding
  total_pnl: number;
  /** The same total by currency: fx_pnl = fx_rate_pnl + conversion_cost. */
  fx_pnl: number;
  fx_rate_pnl: number;
  investment_pnl: number;
}

export interface DayBreakdownRow extends AttributionRow {
  currency: string;
  qty_start: number;
  price_start: number | null; // native
  price_end: number | null;
}

/** One session's breakdown (GET /performance/day), shown by the Best/Worst day cards. */
export interface DayBreakdown {
  date: string;
  prev: string;
  r: number;
  live: boolean;
  rows: DayBreakdownRow[];
  trades: { date: string; ticker: string; action: string; qty: number; price: number; currency: string }[];
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
  source: "wealthsimple";
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
  recompute: { ok: boolean; days?: number; warnings?: number; reason?: string; skipped?: string };
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

// -- Risk analytics (backend services/risk) --------------------------------

export type RiskWindow = "6m" | "1y" | "2y";
export type CovMethod = "lw" | "ewma" | "sample" | "factor";
export interface Empty {
  empty: true;
  reason?: string;
}

export interface RiskHolding {
  asset: string;
  weight: number;
  beta_spy: number | null;
  risk_pct: number;
  proxied: number;
}

export interface RiskSummary {
  empty: false;
  as_of: string;
  n: number;
  total_cad: number;
  vol: number;
  var95: number;
  es975: number;
  beta_spy: number | null;
  beta_xiu: number | null;
  enb: number | null;
  effective_n: number | null;
  diversification_ratio: number | null;
  invested: number;
  drawdown: number | null;
  holdings: RiskHolding[];
}

export interface CovRow {
  asset: string;
  weight: number;
  vol: number;
  beta: number;
  mctr: number;
  ctr: number;
  pct: number;
  bet_share: number;
  proxied: number;
}

export interface CovarianceResult {
  empty: false;
  method: CovMethod;
  window: RiskWindow;
  n: number;
  start: string;
  end: string;
  assets: string[];
  corr: number[][];
  stress_corr: number[][];
  avg_corr: number | null;
  avg_stress_corr: number | null;
  stress_days: number;
  vol: number;
  diversification_ratio: number | null;
  enb_pca: number | null;
  enb_torsion: number | null;
  effective_n: number | null;
  rows: CovRow[];
}

export interface VarMethod {
  var: number | null;
  es: number | null;
  nu?: number | null;
}

export interface Backtest {
  method: string;
  n: number;
  exceptions: number;
  expected: number;
  kupiec: { lr: number | null; p_value: number | null };
  christoffersen: { lr: number; p_value: number; clustered: number } | null;
  var: number[];
}

export interface VarResult {
  empty: false;
  conf: number;
  horizon: number;
  n: number;
  total_cad: number;
  vol: number;
  methods: Record<"normal" | "cornish_fisher" | "student_t" | "historical" | "fhs", VarMethod>;
  components: { asset: string; var: number; es: number; weight: number }[];
  hist: { edges: number[]; counts: number[] };
  backtest: { fhs: Backtest; historical: Backtest; dates: string[]; returns: number[] } | null;
}

export interface FactorExposure {
  asset: string;
  n: number;
  alpha_annual?: number;
  r2?: number;
  specific_vol?: number;
  betas: Record<string, { beta: number; t: number }> | null;
}

export interface FactorsResult {
  empty: false;
  window: RiskWindow;
  n: number;
  factors: { key: string; label: string }[];
  holdings: FactorExposure[];
  portfolio: {
    vol: number;
    factor_share: number | null;
    specific_share: number | null;
    factors: { key: string; exposure: number; risk_share: number | null; factor_vol: number }[];
  };
}

export interface ScenarioHolding {
  asset: string;
  return: number | null;
  method: "replay" | "factor" | "unavailable";
  weight: number;
}

export interface Scenario {
  key: string;
  label: string;
  start: string;
  end: string;
  sessions: number;
  portfolio: number;
  pnl_cad: number;
  market: number;
  usdcad: number;
  worst: string | null;
  factor_mapped: string[];
  holdings: ScenarioHolding[];
}

export interface DriverMeta {
  key: string;
  label: string;
  unit: "bp" | "pct" | "pts";
  default: number;
}

export interface MonteCarlo {
  days: number;
  paths: number;
  median: number;
  p_loss_10: number;
  p_loss_20: number;
  var95: number;
  es95: number;
  var99: number;
  es99: number;
  hist: { edges: number[]; counts: number[] };
}

export interface StressResult {
  empty: false;
  total_cad: number;
  scenarios: Scenario[];
  monte_carlo: MonteCarlo;
  drivers: DriverMeta[];
}

export interface ShockResult {
  empty: false;
  total_cad: number;
  shocks: Record<string, number>;
  causal: {
    portfolio: number;
    direct: number;
    indirect: number;
    ci90: [number, number] | null;
    channels: { path: string[]; contribution: number }[];
  };
  correlational: { portfolio: number };
  drivers: { key: string; label: string; unit: string; shocked: boolean; causal: number; correlational: number }[];
  assets: { asset: string; weight: number; causal: number; correlational: number }[];
  n: number;
}

export interface ReverseResult {
  empty: false;
  loss: number;
  horizon: number;
  total_cad: number;
  drivers: { key: string; label: string; unit: string; move: number }[];
  driver_distance: number | null;
  driver_r2: number;
  driver_every_years: number | null;
  assets: { asset: string; move: number; weight: number }[];
  asset_distance: number | null;
  asset_every_years: number | null;
}

export interface CausalImpact {
  key: string;
  unit: string;
  shock: number;
  portfolio: number;
  direct: number;
  indirect: number;
  ci90: [number, number] | null;
  correlational: number;
  direct_loading: number;
}

export interface CausalGraph {
  empty: false;
  n: number;
  start: string;
  end: string;
  nodes: { key: string; label: string; unit: string }[];
  edges: { from: string; to: string; coef: number; std_coef: number | null }[];
  impacts: CausalImpact[];
  discovery: {
    n: number;
    tau_max: number;
    alpha: number;
    contemporaneous: { a: string; b: string; r: number; p: number }[];
    lagged: { from: string; to: string; lag: number; r: number; p: number }[];
    confirmed: string[][];
    not_found: string[][];
    unexpected: string[][];
  };
  r2: number | null;
}

export interface IrfResult {
  empty: false;
  driver: string;
  label: string;
  unit: string;
  shock: number;
  horizon: number;
  portfolio: { beta: number[]; lo: number[]; hi: number[]; n: number[] };
  assets: { asset: string; beta: number[] }[];
  n: number;
}

export interface PerfMetricsStats {
  n: number;
  total_return?: number;
  cagr?: number;
  annualized?: boolean;
  vol?: number;
  downside_dev?: number;
  sharpe?: number;
  sortino?: number | null;
  calmar?: number | null;
  omega?: number | null;
  skew?: number;
  excess_kurtosis?: number;
  psr?: number | null;
  min_trl_years?: number | null;
  track_years?: number;
  hit_rate?: number;
  var95_hist?: number;
  es95_hist?: number;
  rf_annual?: number;
  max_drawdown?: number;
  peak?: string;
  trough?: string;
  recovered?: string | null;
  longest_underwater_days?: number;
  current_drawdown?: number;
  ulcer_index?: number;
  benchmark?: {
    n: number;
    total_return: number;
    beta: number | null;
    alpha: number | null;
    correlation: number;
    tracking_error: number;
    information_ratio: number | null;
    up_capture: number | null;
    down_capture: number | null;
  };
}

export interface PerfMetrics {
  empty: false;
  range: RangeKey;
  benchmark: string;
  start: string;
  end: string;
  live: boolean;
  stats: PerfMetricsStats;
  rolling: { window: number; dates: string[]; vol: (number | null)[]; sharpe: (number | null)[]; beta: (number | null)[] | null };
  underwater: { dates: string[]; drawdown: number[] };
  monthly: { year: number; month: number; r: number }[];
}

export interface DecisionHorizon {
  value_add: number;
  percentile: number | null;
  placebo_n: number;
  placebo_median: number | null;
}

export interface Decision {
  date: string;
  legs: { ticker: string; side: "buy" | "sell"; qty: number; cad: number }[];
  gross_cad: number;
  horizons: Record<string, DecisionHorizon | null>;
}

export interface DecisionsResult {
  horizons: number[];
  rows: Decision[];
  summary: Record<string, { n: number; value_add: number; mean_percentile: number | null; beat_median: number | null } | null>;
}

export interface EventResult {
  empty: false;
  date: string;
  dates: string[];
  car: number[];
  lo: number[];
  hi: number[];
  actual: number[];
  predicted: number[];
  p_value: number;
  pre: number;
  post: number;
  regressors: string[];
  placebo: { car: number; p_value: number } | null;
}
