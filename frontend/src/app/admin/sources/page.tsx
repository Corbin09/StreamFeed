"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Run, type RunStatus, type Source, type SourceStatus } from "@/lib/api";
import { fullDate, intervalLabel, sourceLabel, timeAgo } from "@/lib/format";

const INTERVALS = [30, 60, 120, 180, 360, 720, 1440];
// Daily windows offered for once-a-day sources (local time, see backend app/scheduling.py).
const DAILY_WINDOWS = ["07:00-09:00", "11:30-13:30", "19:00-22:00"];

function nextRunLabel(iso: string): string {
  const at = new Date(iso);
  if (at.getTime() <= Date.now()) return "Next run: due now";
  const sameDay = at.toDateString() === new Date().toDateString();
  const time = at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `Next run: ${sameDay ? "today" : at.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })} at ${time}`;
}
const ACTIVE: RunStatus[] = ["queued", "running"];

const STATUS: Record<SourceStatus, { label: string; tone: "ok" | "idle" | "bad" }> = {
  idle: { label: "Not run yet", tone: "idle" },
  ok: { label: "Healthy", tone: "ok" },
  needs_login: { label: "Needs login", tone: "bad" },
  error: { label: "Failing", tone: "bad" },
};

const RUN_STATUS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  success: "Succeeded",
  failed: "Failed",
  needs_login: "Stopped at login",
};

const TONE = {
  ok: "bg-jade-soft text-jade-strong",
  idle: "bg-track text-muted",
  bad: "bg-vermilion-soft text-vermilion",
};

function Badge({ status }: { status: SourceStatus }) {
  const s = STATUS[status];
  return <span className={`inline-block rounded px-2 py-0.5 text-[13px] font-medium ${TONE[s.tone]}`}>{s.label}</span>;
}

function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-10 shrink-0 rounded-full transition-colors ${checked ? "bg-jade" : "bg-line"}`}
    >
      <span
        className={`absolute left-0 top-0.5 size-5 rounded-full bg-surface shadow transition-transform ${
          checked ? "translate-x-[18px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

function runSummary(run: Run): string {
  if (run.status === "queued") return "Waiting for the worker";
  if (run.status === "running") return `Started ${timeAgo(run.started_at)}`;
  if (run.status === "success") return `${run.inserted} new, ${run.updated} updated`;
  return run.error ?? RUN_STATUS[run.status];
}

function SourceRow({ source, onError }: { source: Source; onError: (msg: string) => void }) {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: ["runs"] });
  };
  const patch = useMutation({
    mutationFn: (body: { enabled?: boolean; interval_minutes?: number; config?: Record<string, unknown> }) =>
      api.updateSource(source.id, body),
    onSuccess: (updated) => qc.setQueryData<Source[]>(["sources"], (all) => all?.map((s) => (s.id === updated.id ? updated : s))),
    onError: (e) => onError(e.message),
  });
  const run = useMutation({
    mutationFn: () => api.runSource(source.id),
    onSettled: refresh,
    onError: (e) => onError(e.message),
  });

  const last = source.last_run;
  const active = last && ACTIVE.includes(last.status);
  const min = source.min_interval_minutes ?? 1;
  const intervals = [...new Set([...INTERVALS.filter((m) => m >= min), source.interval_minutes])].sort((a, b) => a - b);
  const canRun = source.plugin_available && source.status !== "needs_login" && !active && !run.isPending;

  return (
    <li className="grid gap-x-6 gap-y-3 border-b border-line px-4 py-5 sm:px-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1.3fr)_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-semibold">{source.name}</h2>
          <Badge status={source.status} />
        </div>
        <p className="mt-1 text-sm text-muted">
          {sourceLabel(source.type)}
          {source.plugin_available ? `, ${source.post_count} posts` : ", crawler not built yet"}
        </p>
        {source.enabled && source.next_run_at && (
          <p className="mt-1 text-sm text-muted">{nextRunLabel(source.next_run_at)}</p>
        )}
        {source.status === "needs_login" && (
          <p className="mt-2 text-sm">
            Log in on the host with <code className="rounded bg-track px-1 py-0.5 text-[13px]">python -m app.crawlers.login {source.type}</code>, then run again.
          </p>
        )}
        {source.status === "error" && source.last_error && (
          <p className="mt-2 line-clamp-2 text-sm text-vermilion" title={source.last_error}>
            {source.consecutive_failures} failures in a row: {source.last_error}
          </p>
        )}
      </div>

      <div className="flex items-center gap-3">
        <Switch
          checked={source.enabled}
          onChange={(enabled) => patch.mutate({ enabled })}
          label={`Crawl ${source.name} on a schedule`}
        />
        <label className="sr-only" htmlFor={`interval-${source.id}`}>
          {source.daily_window ? `When ${source.name} runs each day` : `How often to crawl ${source.name}`}
        </label>
        {source.daily_window ? (
          <select
            id={`interval-${source.id}`}
            value={source.daily_window}
            disabled={!source.enabled}
            onChange={(e) => patch.mutate({ config: { ...source.config, daily_window: e.target.value } })}
            className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm disabled:text-faint"
          >
            {[...new Set([...DAILY_WINDOWS, source.daily_window])].map((w) => (
              <option key={w} value={w}>
                Daily, {w.replace("-", "–")}
              </option>
            ))}
          </select>
        ) : (
          <select
            id={`interval-${source.id}`}
            value={source.interval_minutes}
            disabled={!source.enabled}
            onChange={(e) => patch.mutate({ interval_minutes: Number(e.target.value) })}
            className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm disabled:text-faint"
          >
            {intervals.map((m) => (
              <option key={m} value={m}>
                {intervalLabel(m)}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="min-w-0 text-sm">
        {last ? (
          <>
            <p>
              <span className={last.status === "success" ? "" : ACTIVE.includes(last.status) ? "text-saffron" : "text-vermilion"}>
                {RUN_STATUS[last.status]}
              </span>{" "}
              <span className="text-muted" title={fullDate(last.started_at)}>
                {timeAgo(last.finished_at ?? last.started_at)}
              </span>
            </p>
            <p className="mt-0.5 truncate text-muted" title={last.error ?? undefined}>
              {runSummary(last)}
            </p>
          </>
        ) : (
          <p className="text-muted">No runs yet</p>
        )}
      </div>

      <button
        type="button"
        disabled={!canRun}
        onClick={() => run.mutate()}
        className="justify-self-start rounded-lg border border-jade px-4 py-1.5 text-sm font-semibold text-jade hover:bg-jade hover:text-white disabled:border-line disabled:text-faint disabled:hover:bg-transparent lg:justify-self-end dark:hover:text-paper"
      >
        {active ? RUN_STATUS[last!.status] : run.isPending ? "Queuing…" : "Run now"}
      </button>
    </li>
  );
}

function RunsTable({ runs }: { runs: Run[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead className="text-muted">
          <tr className="border-b border-line">
            {["Source", "Started", "Trigger", "Result", "Duration", "Pages", "New", "Updated"].map((h, i) => (
              <th key={h} scope="col" className={`px-3 py-2 font-medium ${i >= 4 ? "text-right" : ""}`}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular">
          {runs.map((r) => (
            <tr key={r.id} className="border-b border-line align-top">
              <td className="px-3 py-2.5">{r.source.name}</td>
              <td className="px-3 py-2.5 text-muted" title={fullDate(r.started_at)}>
                {timeAgo(r.started_at)}
              </td>
              <td className="px-3 py-2.5 text-muted">{r.trigger === "manual" ? "Run now" : "Schedule"}</td>
              <td className="max-w-[18rem] px-3 py-2.5">
                <span className={r.status === "success" ? "" : ACTIVE.includes(r.status) ? "text-saffron" : "text-vermilion"}>
                  {RUN_STATUS[r.status]}
                </span>
                {r.error && <p className="mt-0.5 line-clamp-2 text-muted" title={r.error}>{r.error}</p>}
              </td>
              <td className="px-3 py-2.5 text-right">{r.duration_seconds != null ? `${Math.round(r.duration_seconds)}s` : ""}</td>
              <td className="px-3 py-2.5 text-right">{r.fetched}</td>
              <td className="px-3 py-2.5 text-right">{r.inserted}</td>
              <td className="px-3 py-2.5 text-right">{r.updated}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SourcesPage() {
  const [error, setError] = useState<string | null>(null);
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: api.sources,
    // Poll while a run is queued or running so its result shows up without a reload.
    refetchInterval: (q) => (q.state.data?.some((s) => s.last_run && ACTIVE.includes(s.last_run.status)) ? 3000 : false),
  });
  const anyActive = sources.data?.some((s) => s.last_run && ACTIVE.includes(s.last_run.status));
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.runs(20), refetchInterval: anyActive ? 3000 : false });
  const worker = useQuery({ queryKey: ["worker-health"], queryFn: api.workerHealth, refetchInterval: 30_000 });

  return (
    <main className="mx-auto max-w-[1280px] px-4 pb-24 pt-8 sm:px-6">
      <h1 className="text-[28px] font-bold tracking-tight">Sources</h1>
      <p className="mt-1 max-w-[70ch] text-muted">
        Turn crawling on or off, choose how often each source runs, and start a run by hand.
      </p>

      {worker.data && worker.data.status !== "ok" && (
        <p className="mt-5 rounded-lg border border-line bg-surface px-4 py-3 text-sm">
          <span className="font-semibold">The worker isn’t running.</span>{" "}
          {worker.data.last_beat_at ? `Last seen ${timeAgo(worker.data.last_beat_at)}. ` : ""}
          Scheduled and queued runs start once it’s back.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-5 flex items-start justify-between gap-4 rounded-lg bg-vermilion-soft px-4 py-3 text-sm text-vermilion">
          {error}
          <button type="button" onClick={() => setError(null)} className="font-medium underline">
            Dismiss
          </button>
        </p>
      )}

      <section className="mt-6">
        {sources.error ? (
          <p className="text-vermilion">{sources.error.message}</p>
        ) : sources.isPending ? (
          <div className="h-40 animate-pulse rounded-xl bg-track" />
        ) : (
          <ul className="border-t border-line">
            {sources.data.map((s) => (
              <SourceRow key={s.id} source={s} onError={setError} />
            ))}
          </ul>
        )}
      </section>

      <section className="mt-12">
        <h2 className="mb-3 text-lg font-semibold">Recent runs</h2>
        {runs.data?.length ? <RunsTable runs={runs.data} /> : <p className="text-sm text-muted">Nothing has run yet.</p>}
      </section>
    </main>
  );
}
