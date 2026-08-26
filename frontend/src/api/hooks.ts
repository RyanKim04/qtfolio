import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Account,
  Allocation,
  Attribution,
  CompareResult,
  Currency,
  Health,
  Holdings,
  ImportPreview,
  ImportResult,
  PerfIndexedSeries,
  PerfStackedSeries,
  PerfValueSeries,
  RangeKey,
  RsSeries,
  SettingsData,
  StockCandlesPayload,
  StockLevels,
  StockPosition,
  Summary,
  TestResult,
} from "./types";

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
    // Poll fast while a job is reporting progress (e.g. bootstrap), else 30s.
    refetchInterval: (query) =>
      Object.keys(query.state.data?.progress ?? {}).length > 0 ? 2_500 : 30_000,
  });
}

export function useSummary(currency: Currency) {
  return useQuery({
    queryKey: ["summary", currency],
    queryFn: () => api<Summary>(`/portfolio/summary?currency=${currency}`),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useHoldings(currency: Currency) {
  return useQuery({
    queryKey: ["holdings", currency],
    queryFn: () => api<Holdings>(`/portfolio/holdings?currency=${currency}`),
    refetchInterval: 60_000,
    retry: 1,
  });
}

export function useAllocation(by: string, currency: Currency) {
  return useQuery({
    queryKey: ["allocation", by, currency],
    queryFn: () => api<Allocation>(`/portfolio/allocation?by=${by}&currency=${currency}`),
    refetchInterval: 120_000,
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

export function usePasteToken() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (refresh_token: string) =>
      api<{ ok: boolean; accounts: Account[] }>("/settings/qt-token", {
        method: "POST",
        body: JSON.stringify({ refresh_token }),
      }),
    onSuccess: invalidate,
  });
}

export function useTestConnection() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: () => api<TestResult>("/settings/qt-test", { method: "POST" }),
    onSuccess: invalidate,
  });
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
    retry: 1,
  });
}

export function usePerfIndexed(range: RangeKey, enabled: boolean) {
  return useQuery({
    queryKey: ["perf", "indexed", range],
    queryFn: () => api<PerfIndexedSeries>(`/performance/series?range=${range}&mode=indexed`),
    enabled,
    retry: 1,
  });
}

export function usePerfStacked(range: RangeKey, enabled: boolean) {
  return useQuery({
    queryKey: ["perf", "stacked", range],
    queryFn: () => api<PerfStackedSeries>(`/performance/series?range=${range}&mode=stacked`),
    enabled,
    retry: 1,
  });
}

export function useAttribution(range: RangeKey) {
  return useQuery({
    queryKey: ["perf", "attribution", range],
    queryFn: () => api<Attribution>(`/performance/attribution?range=${range}`),
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
  });
}

async function upload<T>(path: string, file: File, commit: boolean): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  form.append("commit", String(commit));
  const res = await fetch(`/api${path}`, { method: "POST", body: form });
  if (!res.ok) throw new Error(`upload failed: HTTP ${res.status}`);
  return res.json() as Promise<T>;
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
