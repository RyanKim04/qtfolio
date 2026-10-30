import { useRef, useState } from "react";
import {
  useHealth,
  useImportCommit,
  useImportPreview,
  usePasteToken,
  useRetireQuestrade,
  useRunJob,
  useSetAccounts,
  useSettingsData,
  useTestConnection,
  useTfsa,
  useTfsaConfig,
} from "../api/hooks";
import { api } from "../api/client";
import { useQueryClient } from "@tanstack/react-query";
import { fmtRelTime } from "../lib/format";

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card overflow-hidden">
      <h2 className="border-b border-border px-3 py-2 text-[13px] font-medium">{title}</h2>
      <div className="p-3">{children}</div>
    </section>
  );
}

function Onboarding({ step, onBootstrap, bootstrapping }: { step: 1 | 2 | 3; onBootstrap: () => void; bootstrapping: boolean }) {
  const steps = [
    "Paste a Questrade refresh token",
    "Pick the accounts to track",
    "Run bootstrap to backfill history",
  ];
  return (
    <div className="rounded-lg border border-accent/30 bg-accent-soft px-4 py-3">
      <p className="text-[13px] font-medium text-accent">Set up qtfolio</p>
      <ol className="mt-2 space-y-1 text-[13px]">
        {steps.map((s, i) => (
          <li key={s} className={i + 1 === step ? "text-text" : "text-muted"}>
            <span className="num mr-2">{i + 1 <= step - 1 ? "✓" : `${i + 1}.`}</span>
            {s}
            {i === 2 && step === 3 && (
              <button
                onClick={onBootstrap}
                disabled={bootstrapping}
                className="ml-3 rounded-md bg-accent px-2.5 py-0.5 text-[12px] font-medium text-white hover:bg-accent-deep disabled:opacity-50"
              >
                {bootstrapping ? "Running…" : "Run bootstrap"}
              </button>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

function ImportCard() {
  const health = useHealth();
  const preview = useImportPreview();
  const commit = useImportCommit();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);

  const onSelect = (f: File | null) => {
    setFile(f);
    commit.reset();
    if (f) preview.mutate(f);
  };

  const last = health.data?.wealthsimple?.last_import;
  const p = preview.data;
  return (
    <Panel title="Wealthsimple — import activities">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          onSelect(e.dataTransfer.files?.[0] ?? null);
        }}
        className={`space-y-2 rounded-md text-[13px] ${dragging ? "outline-2 outline-dashed outline-accent" : ""}`}
      >
        <p className="text-muted">
          Wealthsimple has no API — holdings move only when a fresh export lands here. In Wealthsimple: Activity →
          Download activities → pick the account → CSV. Drop the file on this card (or choose it) after you trade.
          Overlapping exports are safe; duplicates are skipped. Questrade CSV/XLSX exports work here too.
        </p>
        <div className="num text-[12px] text-muted">
          {last
            ? `Last import ${fmtRelTime(last.at)} · activity through ${last.through ?? "—"} · ${last.imported} new rows`
            : "No Wealthsimple import yet."}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.xlsx,.xls"
          onChange={(e) => onSelect(e.target.files?.[0] ?? null)}
          className="block text-[13px] text-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-panel file:px-3 file:py-1.5 file:text-[13px] file:text-text hover:file:bg-bg file:border-solid"
        />
        {preview.isPending && <p className="text-muted">Parsing…</p>}
        {preview.isError && <p className="text-down">{(preview.error as Error).message}</p>}
        {p && !commit.data && (
          <div className="rounded-md border border-border bg-bg p-3">
            <div className="num">
              {p.source === "wealthsimple" ? "Wealthsimple" : "Questrade"} export · {p.n_rows} rows ·{" "}
              {p.date_range ? `${p.date_range[0]} → ${p.date_range[1]}` : "no dates"} · accounts{" "}
              {p.accounts.join(", ") || "—"}
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {Object.entries(p.by_type).map(([t, n]) => (
                <span key={t} className="num rounded-full border border-border bg-panel px-2 py-0.5 text-[11px] text-muted">
                  {t}: {n}
                </span>
              ))}
            </div>
            {p.unmapped_headers.length > 0 && (
              <p className="mt-1 text-[12px] text-muted">Ignored columns: {p.unmapped_headers.join(", ")}</p>
            )}
            {p.errors.map((e) => (
              <p key={e} className="mt-1 text-[12px] text-down">{e}</p>
            ))}
            <button
              onClick={() => file && commit.mutate(file)}
              disabled={commit.isPending || p.n_rows === 0}
              className="mt-2 rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-white hover:bg-accent-deep disabled:opacity-50"
            >
              {commit.isPending ? "Importing…" : `Import ${p.n_rows} rows`}
            </button>
          </div>
        )}
        {commit.data && (
          <p className="text-up">
            Imported {commit.data.imported} new rows ({commit.data.duplicates} duplicates skipped).{" "}
            {commit.data.recompute.ok
              ? `History recomputed: ${commit.data.recompute.days} days.`
              : `Recompute: ${commit.data.recompute.reason}`}
          </p>
        )}
      </div>
    </Panel>
  );
}

function ListSetting({
  label, settingKey, fallback, upper = true, value, dirty, onChange, onSave,
}: {
  label: string;
  settingKey?: string;
  fallback?: string[];
  upper?: boolean;
  value?: string;
  dirty?: boolean;
  onChange?: (v: string) => void;
  onSave?: () => void;
}) {
  const qc = useQueryClient();
  const settingsData = useSettingsData();
  const [local, setLocal] = useState<string | null>(null);

  const controlled = value !== undefined;
  const stored = settingKey ? ((settingsData.data?.settings[settingKey] as string[]) ?? fallback ?? []) : [];
  const shown = controlled ? value! : (local ?? stored.join(", "));

  const save = async () => {
    if (controlled) return onSave?.();
    const list = shown.split(",").map((s) => (upper ? s.trim().toUpperCase() : s.trim())).filter(Boolean);
    await api("/settings", { method: "PUT", body: JSON.stringify({ [settingKey!]: list }) });
    setLocal(null);
    qc.invalidateQueries();
  };

  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wide text-muted">{label} (comma-separated)</span>
      <div className="mt-1 flex gap-2">
        <input
          value={shown}
          onChange={(e) => (controlled ? onChange?.(e.target.value) : setLocal(e.target.value))}
          className="num flex-1 rounded-md border border-border bg-panel px-2.5 py-1.5 text-[13px] focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
        />
        <button
          onClick={save}
          disabled={controlled ? !dirty : local === null}
          className="rounded-md border border-border bg-panel px-3 py-1.5 text-[13px] text-text hover:bg-bg disabled:opacity-40"
        >
          Save
        </button>
      </div>
    </label>
  );
}

function TfsaSettings() {
  const tfsa = useTfsa();
  const save = useTfsaConfig();
  const [limits, setLimits] = useState<Record<string, string> | null>(null);
  const [anchorYear, setAnchorYear] = useState<string | null>(null);
  const [anchorRoom, setAnchorRoom] = useState<string | null>(null);
  const t = tfsa.data;

  const stored = Object.fromEntries(Object.entries(t?.limits ?? {}).map(([y, v]) => [y, v == null ? "" : String(v)]));
  const shownLimits = limits ?? stored;
  const years = Object.keys(shownLimits).sort();
  const shownYear = anchorYear ?? String(t?.anchor?.year ?? (t ? Number(t.today.slice(0, 4)) : ""));
  const shownRoom = anchorRoom ?? (t?.anchor ? String(t.anchor.room) : "");
  const dirty = limits !== null || anchorYear !== null || anchorRoom !== null;

  const onSave = () =>
    save.mutate(
      {
        limits: Object.fromEntries(years.map((y) => [y, shownLimits[y].trim() === "" ? null : shownLimits[y].trim()])),
        anchor: shownRoom.trim() === "" ? null : { year: Number(shownYear), room: shownRoom.trim() },
      },
      {
        onSuccess: () => {
          setLimits(null);
          setAnchorYear(null);
          setAnchorRoom(null);
        },
      },
    );

  const field = "num rounded-md border border-border bg-panel px-2.5 py-1.5 text-[13px] focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20";
  return (
    <div className="space-y-3 text-[13px]">
      <p className="text-muted">
        Room is per person across every institution. The dashboard works forward from CRA's confirmed Jan 1 room
        (CRA My Account → TFSA) using the transactions you record for that year onward.
      </p>
      <div>
        <div className="text-[11px] uppercase tracking-wide text-muted">CRA-confirmed room on Jan 1</div>
        <div className="mt-1 flex items-center gap-2">
          <input type="number" value={shownYear} onChange={(e) => setAnchorYear(e.target.value)} className={`${field} w-24`} />
          <input
            type="number"
            step="0.01"
            placeholder="Room (CAD)"
            value={shownRoom}
            onChange={(e) => setAnchorRoom(e.target.value)}
            className={`${field} w-40`}
          />
        </div>
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wide text-muted">
          Annual limits — blank = not announced yet (usually November)
        </div>
        <div className="mt-1 grid grid-cols-4 gap-2">
          {years.map((y) => (
            <label key={y} className="flex items-center gap-1.5">
              <span className="num w-10 text-muted">{y}</span>
              <input
                type="number"
                step="0.01"
                placeholder="TBA"
                value={shownLimits[y]}
                onChange={(e) => setLimits({ ...shownLimits, [y]: e.target.value })}
                className={`${field} w-full min-w-0`}
              />
            </label>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={onSave}
          disabled={!dirty || save.isPending}
          className="rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-white hover:bg-accent-deep disabled:opacity-50"
        >
          Save
        </button>
        <button
          onClick={() => setLimits({ ...shownLimits, [String(Math.max(...years.map(Number)) + 1)]: "" })}
          disabled={!years.length}
          className="rounded-md border border-border bg-panel px-3 py-1.5 text-[13px] hover:bg-bg disabled:opacity-40"
        >
          Add year
        </button>
        {save.isError && <span className="text-down">{(save.error as Error).message}</span>}
      </div>
    </div>
  );
}

const statusLabel: Record<string, { text: string; cls: string }> = {
  ok: { text: "● connected", cls: "text-up" },
  absent: { text: "no token", cls: "text-muted" },
  broken: { text: "broken — paste a fresh token", cls: "text-down" },
};

export function Settings() {
  const health = useHealth();
  const settingsData = useSettingsData();
  const paste = usePasteToken();
  const test = useTestConnection();
  const retire = useRetireQuestrade();
  const setAccounts = useSetAccounts();
  const runJob = useRunJob();
  const [token, setToken] = useState("");
  const retired = health.data?.questrade?.retired;

  const qc = useQueryClient();
  const [benchmarks, setBenchmarks] = useState<string | null>(null);

  const t = health.data?.token;
  const ob = health.data?.onboarding;
  const showOnboarding = ob && (!ob.has_token || !ob.has_accounts || !ob.has_history);
  const accounts = settingsData.data?.accounts ?? [];
  const warnings = health.data?.warnings ?? [];
  const bootstrapping = Boolean(health.data?.progress?.bootstrap) || (runJob.isPending && runJob.variables === "bootstrap");
  const benchmarksValue =
    benchmarks ?? ((settingsData.data?.settings.benchmarks as string[]) ?? ["QQQ", "SMH", "SPY"]).join(", ");

  const saveBenchmarks = async () => {
    const list = benchmarksValue.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    await api("/settings", { method: "PUT", body: JSON.stringify({ benchmarks: list }) });
    setBenchmarks(null);
    qc.invalidateQueries();
  };

  const expiresIn = t?.access_expires_at ? fmtRelTime(t.access_expires_at) : null;

  return (
    <div className="max-w-3xl space-y-4">
      {showOnboarding && (
        <Onboarding
          step={!ob.has_token ? 1 : !ob.has_accounts ? 2 : 3}
          onBootstrap={() => runJob.mutate("bootstrap")}
          bootstrapping={bootstrapping}
        />
      )}

      {warnings.length > 0 && (
        <div className="rounded-lg border border-accent/40 bg-accent/5 px-4 py-3">
          <p className="text-[13px] font-medium text-accent">
            Reconciliation notes ({warnings.length})
          </p>
          <p className="mt-0.5 text-[12px] text-muted">
            Anything the reconstruction couldn't fully explain is listed here — with how it was handled —
            instead of being silently adjusted. Nothing to do unless a note looks wrong to you.
          </p>
          <ul className="mt-2 max-h-40 space-y-0.5 overflow-auto text-[12px]">
            {warnings.slice(0, 20).map((w, i) => (
              <li key={i} className="num text-muted">
                {w.date ?? "—"} {w.ticker ? `· ${w.ticker}` : ""} — {w.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ImportCard />

      {retired ? (
        <Panel title="Questrade">
          <p className="text-[13px] text-muted">
            <span className="text-text">Retired {retired.at}</span> — history frozen
            {retired.closing_snapshot ? ` at the closing snapshot of ${retired.closing_snapshot}` : ""}. It stays in
            every chart and in lifetime P&L; token refresh, sync jobs and the broken-token banner are off. Market
            data comes from Yahoo Finance.
          </p>
        </Panel>
      ) : (
      <Panel title="Questrade">
        <div className="space-y-3 text-[13px]">
          <div className="grid grid-cols-4 gap-2">
            <div>
              <div className="text-[11px] uppercase tracking-wide text-muted">Status</div>
              <div className={statusLabel[t?.status ?? "absent"].cls}>{statusLabel[t?.status ?? "absent"].text}</div>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wide text-muted">Access expires</div>
              <div className="num">{t?.status === "ok" ? (expiresIn ?? "—") : "—"}</div>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wide text-muted">Last refresh</div>
              <div className="num">{t?.updated_at ? fmtRelTime(t.updated_at) : "—"}</div>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wide text-muted">API server</div>
              <div className="num truncate" title={t?.api_server ?? undefined}>
                {t?.api_server?.replace("https://", "") ?? "—"}
              </div>
            </div>
          </div>

          {t?.status === "broken" && (
            <div className="rounded-md border border-down/30 bg-down/5 p-2 text-muted">
              The refresh-token chain was lost. Generate a new manual token: Questrade → API Centre →
              Personal apps → Generate new token, then paste it below.
            </div>
          )}

          <div className="flex gap-2">
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste refresh token"
              className="num flex-1 rounded-md border border-border bg-panel px-2.5 py-1.5 text-[13px] placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
            />
            <button
              onClick={() => paste.mutate(token, { onSuccess: () => setToken("") })}
              disabled={paste.isPending || !token.trim()}
              className="rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-white hover:bg-accent-deep disabled:cursor-not-allowed disabled:opacity-40"
            >
              {paste.isPending ? "Connecting…" : "Save token"}
            </button>
            <button
              onClick={() => test.mutate()}
              disabled={test.isPending || t?.status !== "ok"}
              className="rounded-md border border-border bg-panel px-3 py-1.5 text-[13px] text-text hover:bg-bg disabled:cursor-not-allowed disabled:opacity-40"
            >
              {test.isPending ? "Testing…" : "Test connection"}
            </button>
          </div>
          {paste.isError && <p className="text-down">{(paste.error as Error).message}</p>}
          {paste.isSuccess && <p className="text-up">Token accepted — chain started, accounts synced.</p>}
          {test.data &&
            (test.data.ok ? (
              <p className="text-muted">
                Connection ok — {test.data.accounts?.length} account(s), <span className="num">{test.data.latency_ms} ms</span>.
              </p>
            ) : (
              <p className="text-down">{test.data.error}</p>
            ))}
          <div className="border-t border-border pt-2 text-[12px] text-muted">
            Moved to another broker?{" "}
            <button
              onClick={() => {
                if (confirm("Freeze Questrade? A closing snapshot is taken now, then Questrade sync stops for good.")) {
                  retire.mutate();
                }
              }}
              disabled={retire.isPending || t?.status !== "ok"}
              className="text-accent underline disabled:opacity-40"
            >
              {retire.isPending ? "Freezing…" : "Freeze Questrade history"}
            </button>
          </div>
        </div>
      </Panel>
      )}

      <Panel title="Tracked accounts">
        {accounts.length === 0 ? (
          <p className="text-[13px] text-muted">No accounts yet — paste a token above and they sync automatically.</p>
        ) : (
          <ul className="space-y-1">
            {accounts.map((a) => (
              <li key={a.qt_account_id} className="flex items-center gap-3 text-[13px]">
                <input
                  type="checkbox"
                  checked={a.tracked}
                  onChange={(e) => setAccounts.mutate([{ qt_account_id: a.qt_account_id, tracked: e.target.checked }])}
                  className="accent-(--color-accent)"
                />
                <span className="w-24 text-[11px] uppercase tracking-wide text-muted">{a.broker}</span>
                <span className="num">{a.qt_account_id}</span>
                <span>{a.type ?? "?"}</span>
                <span className="ml-auto text-[11px] text-muted">{a.status ?? ""}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Sync">
        <div className="space-y-1.5">
          {(health.data?.jobs ?? []).map((j) => {
            const live = health.data?.progress?.[j.name];
            return (
              <div key={j.name} className="flex items-center gap-3 rounded-md border border-border bg-bg px-3 py-2 text-[13px]">
                <div className="min-w-0">
                  <div className="num font-medium">{j.name}</div>
                  <div className="truncate text-[11px] text-muted">
                    {live
                      ? `running · ${live}`
                      : j.last_run
                        ? `${j.last_run.status} · ${fmtRelTime(j.last_run.started_at)}${j.last_run.detail ? ` · ${j.last_run.detail}` : ""}`
                        : "never run"}
                  </div>
                </div>
                <button
                  onClick={() => runJob.mutate(j.name)}
                  disabled={runJob.isPending || Boolean(live)}
                  className="ml-auto shrink-0 rounded-md border border-border bg-panel px-2 py-1 text-[12px] text-text hover:bg-bg disabled:opacity-40"
                >
                  Run now
                </button>
              </div>
            );
          })}
        </div>
        <p className="mt-2 text-[11px] text-muted">
          Jobs run automatically: overdue jobs execute at every backend start, on their cron schedule while the
          process is alive, and an hourly catch-up sweep retries anything that failed or was missed. Bootstrap is
          manual-only.
        </p>
      </Panel>

      <Panel title="TFSA contribution room">
        <TfsaSettings />
      </Panel>

      <Panel title="Data sources">
        <div className="space-y-3 text-[13px]">
          <ListSetting label="Benchmarks" value={benchmarksValue} dirty={benchmarks !== null}
                       onChange={(v) => setBenchmarks(v)} onSave={saveBenchmarks} />
        </div>
      </Panel>

      <Panel title="Backups">
        <div className="flex items-center gap-3 text-[13px]">
          <span className="text-muted">
            Weekly SQLite backup to <span className="num">data/backups/</span> (keeps 8). Offsite copies are manual —
            drag one to Drive occasionally.
          </span>
          <button
            onClick={() => runJob.mutate("db_backup")}
            disabled={runJob.isPending}
            className="ml-auto shrink-0 rounded-md border border-border bg-panel px-3 py-1.5 text-[13px] text-text hover:bg-bg disabled:opacity-40"
          >
            Back up now
          </button>
        </div>
      </Panel>

      <Panel title="Diagnostics">
        <div className="grid grid-cols-2 gap-2 text-[13px]">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted">USDCAD (Bank of Canada)</div>
            <div className="num">
              {health.data?.fx ? `${health.data.fx.usdcad.toFixed(4)} · ${health.data.fx.date}` : "not fetched yet"}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-muted">Candle adjustment regime</div>
            <div className="num">{health.data?.candle_adjustment_regime ?? "not probed (M2 bootstrap)"}</div>
          </div>
        </div>
      </Panel>
    </div>
  );
}
