// Series dates are UTC-midnight timestamps. Format them in UTC, or every
// label and tooltip shifts one day back in Toronto time.
export const fmtFullDate = (ts: number) =>
  new Date(ts).toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });

// Explicit, non-repeating axis ticks: short ranges get ~8 day-level ticks
// ("Jul 14"); long ranges get one tick per month boundary, thinned to ≤ 12,
// with the year shown only on January ("Jan 2026").
export function makeTimeAxis(dates: string[]): { ticks: number[]; fmt: (ts: number) => string } {
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
