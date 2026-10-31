import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Account,
  Allocation,
  Attribution,
  CausalGraph,
  CompareResult,
  CovarianceResult,
  CovMethod,
  Currency,
  DayBreakdown,
  DecisionsResult,
  Empty,
  EventResult,
  FactorsResult,
  Health,
  Holdings,
  ImportPreview,
  ImportResult,
  IrfResult,
  PerfIndexedSeries,
  PerfMetrics,
  PerfStackedSeries,
  PerfValueSeries,
  RangeKey,
  ReverseResult,
  RiskSummary,
  RiskWindow,
  RsSeries,
  SettingsData,
  ShockResult,
  StockCandlesPayload,
  StockLevels,
  StockPosition,
  StressResult,
  Summary,
  TfsaRecordResult,
  TfsaSummary,
  TfsaTxnInput,
  VarResult,
} from "./types";

// Backend prices come from Yahoo's streamer (seconds behind the tape), so the
// dashboard polls briskly; each poll is a local, cached computation.
const LIVE_MS = 5_000;
// Performance charts end on a live point too. Their endpoints are heavier, so they poll at half the rate.
const PERF_LIVE_MS = 30_000;

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
    // Poll fast while a job is reporting progress, else every 30 s.
    refetchInterval: (query) =>
      Object.keys(query.state.data?.progress ?? {}).length > 0 ? 2_500 : 30_000,
  });
}

export function useSummary(currency: Currency) {
  return useQuery({
    queryKey: ["summary", currency],
    queryFn: () => api<Summary>(`/portfolio/summary?currency=${currency}`),
    refetchInterval: LIVE_MS,
    retry: 1,
  });
}

export function useHoldings(currency: Currency) {
  return useQuery({
    queryKey: ["holdings", currency],
    queryFn: () => api<Holdings>(`/portfolio/holdings?currency=${currency}`),
    refetchInterval: LIVE_MS,
    retry: 1,
  });
}

export function useAllocation(by: string, currency: Currency) {
  return useQuery({
    queryKey: ["allocation", by, currency],
    queryFn: () => api<Allocation>(`/portfolio/allocation?by=${by}&currency=${currency}`),
    refetchInterval: LIVE_MS,
    retry: 1,
  });
}

export function useSettingsData() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: () => api<SettingsData>("/settings"),
  });
}

function useInvalidateAll() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries();
}

export function useSetAccounts() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (accounts: { qt_account_id: string; tracked: boolean }[]) =>
      api<{ accounts: Account[] }>("/settings/accounts", {
        method: "PUT",
        body: JSON.stringify(accounts),
      }),
    onSuccess: invalidate,
  });
}

export function useRunJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api<{ job: string; status: string }>(`/sync/${name}`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function usePerfValue(range: RangeKey, baseline: "range" | "inception" = "range") {
  return useQuery({
    queryKey: ["perf", "value", range, baseline],
    queryFn: () => api<PerfValueSeries>(`/performance/series?range=${range}&mode=value&baseline=${baseline}`),
    refetchInterval: PERF_LIVE_MS,
    retry: 1,
  });
}

export function usePerfIndexed(range: RangeKey, enabled: boolean) {
  return useQuery({
    queryKey: ["perf", "indexed", range],
    queryFn: () => api<PerfIndexedSeries>(`/performance/series?range=${range}&mode=indexed`),
    enabled,
    refetchInterval: PERF_LIVE_MS,
    retry: 1,
  });
}

export function usePerfStacked(range: RangeKey, enabled: boolean) {
  return useQuery({
    queryKey: ["perf", "stacked", range],
    queryFn: () => api<PerfStackedSeries>(`/performance/series?range=${range}&mode=stacked`),
    enabled,
    refetchInterval: PERF_LIVE_MS,
    retry: 1,
  });
}

export function useAttribution(range: RangeKey) {
  return useQuery({
    queryKey: ["perf", "attribution", range],
    queryFn: () => api<Attribution>(`/performance/attribution?range=${range}`),
    refetchInterval: PERF_LIVE_MS,
    retry: 1,
  });
}

export function usePerfDay(date: string, prev: string, enabled: boolean) {
  return useQuery({
    queryKey: ["perf", "day", date, prev],
    queryFn: () => api<DayBreakdown>(`/performance/day?date=${date}&prev=${prev}`),
    enabled,
    staleTime: 5 * 60_000, // past sessions don't move; fetched on first hover
    retry: 1,
  });
}

export function useStockCandles(ticker: string, interval: string) {
  return useQuery({
    queryKey: ["stock", ticker, "candles", interval],
    queryFn: () => api<StockCandlesPayload>(`/stocks/${ticker}/candles?interval=${interval}`),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useStockLevels(ticker: string) {
  return useQuery({
    queryKey: ["stock", ticker, "levels"],
    queryFn: () => api<StockLevels>(`/stocks/${ticker}/levels`),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useStockPosition(ticker: string) {
  return useQuery({
    queryKey: ["stock", ticker, "position"],
    queryFn: () => api<StockPosition>(`/stocks/${ticker}/position`),
    staleTime: 60_000,
    retry: 1,
  });
}

export function useRs(ticker: string, benchmark: string) {
  return useQuery({
    queryKey: ["stock", ticker, "rs", benchmark],
    queryFn: () => api<RsSeries>(`/stocks/${ticker}/rs?benchmark=${benchmark}`),
    staleTime: 5 * 60_000,
    retry: 1,
  });
}

export function useCompare(symbols: string[], range: RangeKey) {
  return useQuery({
    queryKey: ["compare", symbols.join(","), range],
    queryFn: () => api<CompareResult>(`/performance/compare?symbols=${symbols.join(",")}&range=${range}`),
    enabled: symbols.length > 0,
    retry: 1,
    staleTime: 5 * 60_000,
    refetchInterval: PERF_LIVE_MS, // benchmarks' today point is a live quote
  });
}

async function upload<T>(path: string, file: File, commit: boolean): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  form.append("commit", String(commit));
  // headers: {} drops api()'s JSON Content-Type so the browser sets multipart and its boundary.
  return api<T>(path, { method: "POST", body: form, headers: {} });
}

export function useImportPreview() {
  return useMutation({ mutationFn: (file: File) => upload<ImportPreview>("/import/activities", file, false) });
}

export function useImportCommit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => upload<ImportResult>("/import/activities", file, true),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useTfsa() {
  return useQuery({ queryKey: ["tfsa"], queryFn: () => api<TfsaSummary>("/tfsa") });
}

// Mirrors the activities import: commit=false previews the warnings, commit=true writes.
export function useTfsaRecord() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ commit, ...txn }: TfsaTxnInput & { commit: boolean }) =>
      api<TfsaRecordResult>("/tfsa/transactions", { method: "POST", body: JSON.stringify({ ...txn, commit }) }),
    onSuccess: (r) => {
      if (r.phase === "committed") qc.invalidateQueries({ queryKey: ["tfsa"] });
    },
  });
}

export function useTfsaDelete() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<{ ok: boolean }>(`/tfsa/transactions/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tfsa"] }),
  });
}

export function useTfsaConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (cfg: {
      limits: Record<string, number | string | null>;
      anchor: { year: number; room: number | string } | null;
    }) => api<unknown>("/tfsa/config", { method: "PUT", body: JSON.stringify(cfg) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tfsa"] }),
  });
}

// -- Risk analytics. Results are end-of-day computations cached server-side,
// so they never join the 5 s live poll.

const RISK_STALE = 10 * 60_000;

export function useRiskSummary() {
  return useQuery({
    queryKey: ["risk", "summary"],
    queryFn: () => api<RiskSummary | Empty>("/risk/summary"),
    staleTime: RISK_STALE,
  });
}

export function useRiskCovariance(method: CovMethod, window: RiskWindow) {
  return useQuery({
    queryKey: ["risk", "covariance", method, window],
    queryFn: () => api<CovarianceResult | Empty>(`/risk/covariance?method=${method}&window=${window}`),
    staleTime: RISK_STALE,
  });
}

export function useRiskVar(conf: number, horizon: number) {
  return useQuery({
    queryKey: ["risk", "var", conf, horizon],
    queryFn: () => api<VarResult | Empty>(`/risk/var?conf=${conf}&horizon=${horizon}`),
    staleTime: RISK_STALE,
  });
}

export function useRiskFactors(window: RiskWindow) {
  return useQuery({
    queryKey: ["risk", "factors", window],
    queryFn: () => api<FactorsResult | Empty>(`/risk/factors?window=${window}`),
    staleTime: RISK_STALE,
  });
}

export function useRiskStress() {
  return useQuery({
    queryKey: ["risk", "stress"],
    queryFn: () => api<StressResult | Empty>("/risk/stress"),
    staleTime: RISK_STALE,
  });
}

export function useRiskShock(shocks: Record<string, number>) {
  const active = Object.fromEntries(Object.entries(shocks).filter(([, v]) => v));
  return useQuery({
    queryKey: ["risk", "shock", JSON.stringify(active)],
    queryFn: () =>
      api<ShockResult | Empty>("/risk/stress/custom", { method: "POST", body: JSON.stringify({ shocks: active }) }),
    enabled: Object.keys(active).length > 0,
    staleTime: RISK_STALE,
    placeholderData: (prev) => prev,
  });
}

export function useRiskReverse(loss: number, horizon: number) {
  return useQuery({
    queryKey: ["risk", "reverse", loss, horizon],
    queryFn: () =>
      api<ReverseResult | Empty>("/risk/stress/reverse", { method: "POST", body: JSON.stringify({ loss, horizon }) }),
    staleTime: RISK_STALE,
    placeholderData: (prev) => prev,
  });
}

export function useCausalGraph() {
  return useQuery({
    queryKey: ["risk", "causal", "graph"],
    queryFn: () => api<CausalGraph | Empty>("/risk/causal/graph"),
    staleTime: RISK_STALE,
  });
}

export function useIrf(driver: string) {
  return useQuery({
    queryKey: ["risk", "causal", "irf", driver],
    queryFn: () => api<IrfResult | Empty>(`/risk/causal/irf?driver=${driver}`),
    staleTime: RISK_STALE,
    placeholderData: (prev) => prev,
  });
}

export function usePerfMetrics(range: RangeKey, benchmark: string) {
  return useQuery({
    queryKey: ["perf", "metrics", range, benchmark],
    queryFn: () => api<PerfMetrics | Empty>(`/performance/metrics?range=${range}&benchmark=${encodeURIComponent(benchmark)}`),
    refetchInterval: PERF_LIVE_MS,
    placeholderData: (prev) => prev,
  });
}

export function useDecisions(range: RangeKey) {
  return useQuery({
    queryKey: ["perf", "decisions", range],
    queryFn: () => api<DecisionsResult>(`/performance/decisions?range=${range}`),
    staleTime: RISK_STALE,
  });
}

export function useEvent(date: string | null) {
  return useQuery({
    queryKey: ["perf", "event", date],
    queryFn: () => api<EventResult | Empty>(`/performance/event?date=${date}`),
    enabled: Boolean(date),
    staleTime: RISK_STALE,
  });
}
