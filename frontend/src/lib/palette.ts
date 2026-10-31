// Categorical chart palette: blues, purples and grays only. Green and red are
// reserved for P&L.
export const COLORS = [
  "#635bff",
  "#2e90fa",
  "#84adff",
  "#175cd3",
  "#b692f6",
  "#36bffa",
  "#98a2b3",
  "#667085",
  "#d6bbfb",
];

export const TOOLTIP_STYLE = {
  background: "var(--color-panel)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  boxShadow: "0 2px 8px rgba(16,24,40,.08)",
  fontSize: 12,
  fontVariantNumeric: "tabular-nums",
} as const;
