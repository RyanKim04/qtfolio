import { NavLink } from "react-router-dom";

const items = [
  { to: "/", label: "Dashboard" },
  { to: "/performance", label: "Performance" },
  { to: "/risk", label: "Risk" },
  { to: "/settings", label: "Settings" },
];

export function LeftRail() {
  return (
    <nav className="flex w-48 shrink-0 flex-col border-r border-border bg-panel">
      <div className="px-4 pb-2 pt-4">
        <span className="text-[15px] font-semibold tracking-tight text-accent">qtfolio</span>
      </div>
      <div className="flex flex-col gap-0.5 px-2 py-2">
        {items.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            end={it.to === "/"}
            className={({ isActive }) =>
              `rounded-md px-3 py-1.5 text-[13px] ${
                isActive
                  ? "bg-accent-soft font-medium text-accent"
                  : "text-muted hover:bg-bg hover:text-text"
              }`
            }
          >
            {it.label}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
