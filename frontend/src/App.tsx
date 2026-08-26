import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, createContext, useContext, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { useHealth } from "./api/hooks";
import type { Currency } from "./api/types";
import { BrokenTokenBanner } from "./components/BrokenTokenBanner";
import { Header } from "./components/Header";
import { LeftRail } from "./components/LeftRail";
import { Dashboard } from "./pages/Dashboard";
import { Performance } from "./pages/Performance";
import { Settings } from "./pages/Settings";
import { StockDetail } from "./pages/StockDetail";

const queryClient = new QueryClient();

const CurrencyContext = createContext<{ currency: Currency; setCurrency: (c: Currency) => void }>({
  currency: "CAD",
  setCurrency: () => {},
});

export function useCurrency() {
  return useContext(CurrencyContext);
}

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card m-4 p-6 text-[13px]">
          <p className="font-medium text-down">Something crashed while rendering this page.</p>
          <pre className="num mt-2 overflow-auto rounded-md border border-border bg-bg p-3 text-[11px] text-muted">
            {String(this.state.error?.stack || this.state.error)}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            className="mt-3 rounded-md border border-border bg-panel px-3 py-1.5 text-[13px] hover:bg-bg"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function Layout() {
  const health = useHealth();
  const location = useLocation();

  // Empty DB → route to Settings onboarding (SPEC.md §13).
  if (health.data && !health.data.onboarding.has_token && location.pathname !== "/settings") {
    return <Navigate to="/settings" replace />;
  }

  return (
    <div className="flex h-full">
      <LeftRail />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <BrokenTokenBanner />
        <main className="min-h-0 flex-1 overflow-auto p-4">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const [currency, setCurrency] = useState<Currency>(() => (localStorage.getItem("qtfolio.currency") as Currency) || "CAD");
  const set = (c: Currency) => {
    localStorage.setItem("qtfolio.currency", c);
    setCurrency(c);
  };
  return (
    <QueryClientProvider client={queryClient}>
      <CurrencyContext.Provider value={{ currency, setCurrency: set }}>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="/performance" element={<Performance />} />
              <Route path="/stocks/:ticker" element={<StockDetail />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </CurrencyContext.Provider>
    </QueryClientProvider>
  );
}
