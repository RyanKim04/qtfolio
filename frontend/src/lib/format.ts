export function fmtMoney(v: number | null | undefined, currency: string, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency,
    currencyDisplay: "narrowSymbol",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v);
}

export function fmtSignedMoney(v: number | null | undefined, currency: string, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v > 0 ? "+" : ""}${fmtMoney(v, currency, digits)}`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return new Intl.NumberFormat("en-CA", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v);
}

export function fmtQty(v: number | null | undefined): string {
  if (v == null) return "—";
  return Number.isInteger(v) ? String(v) : fmtNum(v, 4);
}

export function fmtPct(v: number | null | undefined, signed = true): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${signed && v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function pnlClass(v: number | null | undefined): string {
  if (v == null || v === 0) return "text-muted";
  return v > 0 ? "text-up" : "text-down";
}

export function fmtRelTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).getTime();
  const s = Math.round((Date.now() - then) / 1000);
  if (s < 0) return formatIn(-s);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

function formatIn(s: number): string {
  if (s < 60) return `in ${s}s`;
  if (s < 3600) return `in ${Math.floor(s / 60)} min`;
  return `in ${Math.floor(s / 3600)} h`;
}

export function fmtEtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return `${new Date(iso).toLocaleString("en-CA", {
    timeZone: "America/Toronto",
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  })} ET`;
}

/** A fraction (0.0123) as a percent ("+1.23%"). */
export function fmtFrac(v: number | null | undefined, signed = true, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  const p = v * 100;
  return `${signed && p > 0 ? "+" : ""}${p.toFixed(digits)}%`;
}

/** A driver move in its own unit: bp, VIX points or percent. */
export function fmtUnit(v: number | null | undefined, unit: string, digits = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  const s = v > 0 ? "+" : "";
  if (unit === "bp") return `${s}${v.toFixed(0)} bp`;
  if (unit === "pts") return `${s}${v.toFixed(digits)} pts`;
  return `${s}${v.toFixed(digits)}%`;
}
