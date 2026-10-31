import type { ReactNode } from "react";

/** A labeled figure card, the Dashboard and Performance stat tile. */
export function StatCard({ label, value, sub, className = "", title, size = "md" }: {
  label: string; value: string; sub?: ReactNode; className?: string; title?: string; size?: "md" | "lg";
}) {
  return (
    <div className="card px-3 py-2" title={title}>
      <div className="truncate text-[11px] font-medium uppercase tracking-wide text-muted" title={title ?? label}>{label}</div>
      <div className={`num mt-0.5 font-semibold ${size === "lg" ? "text-[17px]" : "text-[15px]"} ${className}`}>{value}</div>
      {sub && <div className="num mt-0.5 truncate text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

export function Seg<T extends string | number>({ value, options, onChange }: {
  value: T; options: readonly { key: T; label: string }[] | readonly T[]; onChange: (v: T) => void;
}) {
  const opts = options.map((o) =>
    typeof o === "object" ? o : { key: o as T, label: typeof o === "string" ? o.toUpperCase() : String(o) },
  );
  return (
    <div className="flex overflow-hidden rounded-md border border-border">
      {opts.map((o) => (
        <button
          key={String(o.key)}
          onClick={() => onChange(o.key)}
          aria-pressed={value === o.key}
          className={`px-2.5 py-1 text-[12px] ${value === o.key ? "bg-accent font-medium text-white" : "bg-panel text-muted hover:text-text"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Card with the standard hairline header: title, a muted note, and controls on the right. */
export function Section({ title, note, right, children, className = "" }: {
  title: string; note?: ReactNode; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`card overflow-hidden ${className}`}>
      <div className="flex items-start justify-between gap-3 border-b border-border px-3 py-2">
        <div className="min-w-0">
          <h2 className="text-[13px] font-medium">{title}</h2>
          {note && <div className="text-[11px] text-muted">{note}</div>}
        </div>
        {right && <div className="flex shrink-0 items-center gap-2">{right}</div>}
      </div>
      {children}
    </section>
  );
}

export function Loading({ what = "" }: { what?: string }) {
  return <p className="py-10 text-center text-[13px] text-muted">Computing{what ? ` ${what}` : ""}…</p>;
}

export function LoadError({ error }: { error: unknown }) {
  return (
    <p className="p-4 text-[13px] text-down">
      Couldn't compute this: {(error as Error)?.message ?? "unknown error"}. Market data comes from Yahoo; try again
      in a minute.
    </p>
  );
}
