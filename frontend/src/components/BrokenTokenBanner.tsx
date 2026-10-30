import { Link, useLocation } from "react-router-dom";
import { useHealth } from "../api/hooks";

export function BrokenTokenBanner() {
  const health = useHealth();
  const location = useLocation();
  // A retired Questrade has no chain to keep alive — its expiry is expected.
  if (health.data?.token.status !== "broken" || health.data?.questrade?.retired) return null;
  return (
    <div className="border-b border-down/40 bg-down/10 px-4 py-2 text-[13px]">
      <span className="font-medium text-down">Questrade token chain is broken.</span>{" "}
      <span className="text-muted">
        Data is served from cache; Questrade sync is paused until a fresh token is pasted.
      </span>{" "}
      {location.pathname !== "/settings" && (
        <Link to="/settings" className="text-accent underline">
          Open Settings
        </Link>
      )}
    </div>
  );
}
