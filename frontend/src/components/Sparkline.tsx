// DECISION: a plain SVG polyline instead of a Recharts instance per row. It
// looks the same and is much lighter with 10+ table rows. The stroke is the
// neutral capital-in gray, since green and red are reserved for P&L.
export function Sparkline({ data, width = 84, height = 24 }: { data: number[]; width?: number; height?: number }) {
  if (!data || data.length < 2) return <span className="text-[11px] text-muted">—</span>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const pts = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * (width - 2) + 1;
      const y = height - 2 - ((v - min) / span) * (height - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={width} height={height} className="block">
      <polyline points={pts} fill="none" stroke="var(--color-capital)" strokeWidth="1.2" />
    </svg>
  );
}
