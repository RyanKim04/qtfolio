import { useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { useAllocation } from "../api/hooks";
import { useCurrency } from "../App";
import { fmtMoney, fmtPct } from "../lib/format";

import { COLORS } from "../lib/palette";

const MODES = [
  { key: "position", label: "Position" },
  { key: "currency", label: "Currency" },
  { key: "sector", label: "Sector" },
];

export function AllocationDonut() {
  const [by, setBy] = useState("position");
  const { currency } = useCurrency();
  const alloc = useAllocation(by, currency);

  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <h2 className="text-[13px] font-medium">Allocation</h2>
        <div className="flex overflow-hidden rounded-md border border-border">
          {MODES.map((m) => {
            const disabled = m.key === "sector";
            return (
              <button
                key={m.key}
                disabled={disabled}
                title={disabled ? "Sector data arrives with the analyst sync (M5)" : undefined}
                onClick={() => setBy(m.key)}
                className={`px-2 py-0.5 text-[12px] ${
                  by === m.key
                    ? "bg-accent font-medium text-white"
                    : disabled
                      ? "cursor-not-allowed text-border"
                      : "text-muted hover:text-text"
                }`}
              >
                {m.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="p-3">
        {alloc.isLoading ? (
          <p className="py-10 text-center text-[13px] text-muted">Loading…</p>
        ) : !alloc.data?.slices.length ? (
          <p className="py-10 text-center text-[13px] text-muted">No positions yet.</p>
        ) : (
          <>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={alloc.data.slices}
                    dataKey="value"
                    nameKey="label"
                    innerRadius="62%"
                    outerRadius="90%"
                    stroke="var(--color-panel)"
                    strokeWidth={1}
                    isAnimationActive={false}
                  >
                    {alloc.data.slices.map((s, i) => (
                      <Cell key={s.label} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-panel)",
                      border: "1px solid var(--color-border)",
                      borderRadius: 6,
                      boxShadow: "0 2px 8px rgba(16,24,40,.08)",
                      fontSize: 12,
                      fontVariantNumeric: "tabular-nums",
                    }}
                    formatter={(value: number, name: string) => [fmtMoney(value, currency), name]}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <ul className="mt-2 space-y-0.5">
              {alloc.data.slices.map((s, i) => (
                <li key={s.label} className="flex items-center gap-2 text-[12px]">
                  <span className="h-2 w-2 shrink-0" style={{ background: COLORS[i % COLORS.length] }} />
                  <span className="w-16 truncate">{s.label}</span>
                  <span className="num ml-auto text-muted">{fmtMoney(s.value, currency)}</span>
                  <span className="num w-14 text-right">{fmtPct(s.pct, false)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
