import { useRef, useState } from "react";
import {
  useHealth,
  useImportCommit,
  useImportPreview,
  useRunJob,
  useSetAccounts,
  useSettingsData,
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
  const watch = health.data?.wealthsimple?.watch;
  const p = preview.data;
  return (
    <Panel title="Import Wealthsimple activities">
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
          In Wealthsimple, go to Activity → Download activities → CSV. Any export saved to{" "}
          <span className="num">{watch?.dir ?? "~/Downloads"}</span> imports within a few seconds, and rows already
          imported are skipped. You can also drop a file here.
        </p>
        {watch?.error && <p className="text-[12px] text-down">Auto-import paused: {watch.error}</p>}
        <div className="num text-[12px] text-muted">
          {last
            ? `Last import ${fmtRelTime(last.at)}${last.file ? ` (${last.file})` : ""} · activity through ${last.through ?? "—"} · ${last.imported} new rows`
            : "No Wealthsimple import yet."}
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".csv"
          onChange={(e) => onSelect(e.target.files?.[0] ?? null)}
          className="block text-[13px] text-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-panel file:px-3 file:py-1.5 file:text-[13px] file:text-text hover:file:bg-bg file:border-solid"
        />
        {preview.isPending && <p className="text-muted">Parsing…</p>}
        {preview.isError && <p className="text-down">{(preview.error as Error).message}</p>}
        {p && !commit.data && (
          <div className="rounded-md border border-border bg-bg p-3">
            <div className="num">
              Wealthsimple export · {p.n_rows} rows ·{" "}
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
            {commit.data.recompute.skipped
              ? "Nothing new."
              : commit.data.recompute.ok
              ? `History recomputed: ${commit.data.recompute.days} days.`
              : `Recompute: ${commit.data.recompute.reason}`}
          </p>
        )}
      </div>
    </Panel>
  );
}

function ListSetting({ label, value, dirty, onChange, onSave }: {
  label: string;
  value: string;
  dirty: boolean;
  onChange: (v: string) => void;
  onSave: () => void;
}) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wide text-muted">{label} (comma-separated)</span>
      <div className="mt-1 flex gap-2">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="num flex-1 rounded-md border border-border bg-panel px-2.5 py-1.5 text-[13px] focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
        />
        <button
          onClick={onSave}
          disabled={!dirty}
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
        TFSA room is per person, across all institutions. The dashboard starts from CRA's Jan 1 room (CRA My
        Account → TFSA) and applies the transactions you record from that year on.
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
          Annual limits (leave blank until announced, usually in November)
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

export function Settings() {
  const health = useHealth();
  const settingsData = useSettingsData();
  const setAccounts = useSetAccounts();
  const runJob = useRunJob();

  const qc = useQueryClient();
  const [benchmarks, setBenchmarks] = useState<string | null>(null);

  const ob = health.data?.onboarding;
  const accounts = settingsData.data?.accounts ?? [];
  const warnings = health.data?.warnings ?? [];
  const benchmarksValue =
    benchmarks ?? ((settingsData.data?.settings.benchmarks as string[]) ?? ["QQQ", "SMH", "SPY", "XEQT.TO"]).join(", ");

  const saveBenchmarks = async () => {
    const list = benchmarksValue.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    await api("/settings", { method: "PUT", body: JSON.stringify({ benchmarks: list }) });
    setBenchmarks(null);
    qc.invalidateQueries();
  };

  return (
    <div className="max-w-3xl space-y-4">
      {ob && !ob.has_history && (
        <div className="rounded-lg border border-accent/30 bg-accent-soft px-4 py-3 text-[13px]">
          <p className="font-medium text-accent">Set up qtfolio</p>
          <p className="mt-1 text-muted">Import a Wealthsimple activities export below to build your history.</p>
        </div>
      )}

      {warnings.length > 0 && (
        <div className="rounded-lg border border-accent/40 bg-accent/5 px-4 py-3">
          <p className="text-[13px] font-medium text-accent">
            Reconciliation notes ({warnings.length})
          </p>
          <p className="mt-0.5 text-[12px] text-muted">
            Events the history rebuild couldn't fully explain, and how each one was handled. No action needed
            unless a note looks wrong.
          </p>
          <ul className="mt-2 max-h-40 space-y-0.5 overflow-auto text-[12px]">
            {warnings.slice(0, 20).map((w, i) => (
              <li key={i} className="num text-muted">
                {w.date ?? "—"} {w.ticker ? `· ${w.ticker} ` : ""}· {w.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ImportCard />

      <Panel title="Tracked accounts">
        {accounts.length === 0 ? (
          <p className="text-[13px] text-muted">No accounts yet. Import an export above.</p>
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
          Overdue jobs run when the backend starts, then on their cron schedule. Every hour, any job that failed or
          was missed runs again.
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
            Weekly SQLite backup to <span className="num">data/backups/</span>, keeping the last 8. Nothing is copied
            offsite, so copy a backup to Drive yourself.
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
        <div className="text-[13px]">
          <div className="text-[11px] uppercase tracking-wide text-muted">USDCAD (Bank of Canada)</div>
          <div className="num">
            {health.data?.fx ? `${health.data.fx.usdcad.toFixed(4)} · ${health.data.fx.date}` : "not fetched yet"}
          </div>
        </div>
      </Panel>
    </div>
  );
}
