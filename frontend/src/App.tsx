import {
  Activity,
  AlertTriangle,
  BarChart3,
  Beaker,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Database,
  Download,
  FileSearch,
  FlaskConical,
  Pause,
  Play,
  RotateCw,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  Square,
  TerminalSquare,
  XCircle
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type {
  Catalog,
  EvaluationResults,
  Health,
  ResultRecordPage,
  RunView,
  WorkItem
} from "./types";

type Mode = "benchmark" | "query";
const terminalStatuses = new Set(["completed", "failed", "cancelled"]);

function StatusCard({ icon, label, value, ok }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  ok: boolean;
}) {
  return (
    <article className="status-card">
      <div className={`status-icon ${ok ? "ok" : "warn"}`}>{icon}</div>
      <div><span>{label}</span><strong>{value}</strong></div>
      <CircleDot className={ok ? "signal-ok" : "signal-warn"} size={14} />
    </article>
  );
}

function RunStatusPill({ status }: { status: string }) {
  return <span className={`run-status status-${status}`}><i />{status.replace("_", " ")}</span>;
}

function countLabel(value: number, singular: string) {
  return `${value.toLocaleString()} ${value === 1 ? singular : `${singular}s`}`;
}

export default function App() {
  const [mode, setMode] = useState<Mode>("benchmark");
  const [health, setHealth] = useState<Health | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [runs, setRuns] = useState<RunView[]>([]);
  const [activeRun, setActiveRun] = useState<RunView | null>(null);
  const [items, setItems] = useState<WorkItem[]>([]);
  const [results, setResults] = useState<EvaluationResults | null>(null);
  const [selectedMethods, setSelectedMethods] = useState<string[]>(["direct", "sag_v3"]);
  const [selectedTracks, setSelectedTracks] = useState<string[]>(["canonical"]);
  const [concurrency, setConcurrency] = useState(0);
  const [model, setModel] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState("medium");
  const [database, setDatabase] = useState("");
  const [question, setQuestion] = useState("");
  const [executeCustom, setExecuteCustom] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshSystem = useCallback(async () => {
    const [healthValue, catalogValue, runValues] = await Promise.all([
      api.health(), api.catalog(), api.runs()
    ]);
    setHealth(healthValue);
    setCatalog(catalogValue);
    setRuns(runValues);
    setConcurrency((current) => current || healthValue.defaults.concurrency);
    setModel((current) => current || healthValue.provider.model);
    setReasoningEffort((current) => current || healthValue.provider.reasoning_effort);
    setDatabase((current) => current || catalogValue.dataset.databases?.[0] || "");
  }, []);

  const refreshRun = useCallback(async (runId: string) => {
    const [run, workItems] = await Promise.all([api.run(runId), api.items(runId, 100)]);
    setActiveRun(run);
    setItems(workItems);
    setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
    if (run.mode === "benchmark") {
      const evaluation = await api.results(runId);
      setResults(evaluation);
    } else {
      setResults(null);
    }
    return run;
  }, []);

  useEffect(() => {
    refreshSystem().catch((value) => setError(String(value)));
  }, [refreshSystem]);

  useEffect(() => {
    if (!activeRun) return;
    const evaluationDone = activeRun.mode === "custom_query"
      || activeRun.status !== "completed"
      || (results?.run_id === activeRun.id && ["completed", "failed"].includes(results.status));
    if (terminalStatuses.has(activeRun.status) && evaluationDone) return;
    const interval = window.setInterval(() => {
      refreshRun(activeRun.id).catch((value) => setError(String(value)));
    }, 900);
    return () => window.clearInterval(interval);
  }, [activeRun?.id, activeRun?.status, results?.run_id, results?.status, refreshRun]);

  const plannedTasks = useMemo(() => {
    const perTrack = catalog?.dataset.task_count ?? 1210;
    return selectedMethods.length * selectedTracks.length * perTrack;
  }, [catalog, selectedMethods.length, selectedTracks.length]);

  const toggle = (id: string, current: string[], update: (next: string[]) => void) => {
    update(current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  };

  const startRun = async () => {
    if (!health?.execution.available) {
      setError(health?.execution.message ?? "Method execution is not configured.");
      return;
    }
    if (!selectedMethods.length) {
      setError("Select at least one method.");
      return;
    }
    if (mode === "benchmark" && !selectedTracks.length) {
      setError("Select at least one evaluation track.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const run = mode === "benchmark"
        ? await api.createRun({
            mode: "benchmark",
            method_ids: selectedMethods,
            tracks: selectedTracks,
            concurrency,
            model,
            reasoning_effort: reasoningEffort as "none" | "low" | "medium" | "high" | "xhigh" | "max"
          })
        : await api.createRun({
            mode: "custom_query",
            method_ids: selectedMethods,
            database_id: database,
            question,
            execute_custom_query: executeCustom,
            concurrency,
            model,
            reasoning_effort: reasoningEffort as "none" | "low" | "medium" | "high" | "xhigh" | "max"
          });
      setActiveRun(run);
      setItems([]);
      setResults(null);
      setRuns((current) => [run, ...current]);
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value));
    } finally {
      setBusy(false);
    }
  };

  const controlRun = async (action: "pause" | "resume" | "cancel") => {
    if (!activeRun) return;
    setBusy(true);
    try {
      const updated = await api[action](activeRun.id);
      setActiveRun(updated);
      await refreshRun(activeRun.id);
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Beaker size={21} /></div>
          <div><strong>TEND Lab</strong><span>Evaluation control plane</span></div>
        </div>
        <nav aria-label="Primary navigation">
          <button className="active" onClick={() => document.getElementById("runs")?.scrollIntoView({ behavior: "smooth" })}><Activity size={16} /> Runs</button>
          <button onClick={() => document.getElementById("results")?.scrollIntoView({ behavior: "smooth" })}><BarChart3 size={16} /> Results</button>
          <button onClick={() => document.getElementById("settings")?.scrollIntoView({ behavior: "smooth" })}><Settings2 size={16} /> Settings</button>
        </nav>
        <button className="refresh-button" onClick={() => refreshSystem()} title="Refresh system">
          <RotateCw size={15} />
        </button>
        <div className={`readiness ${health?.status === "ready" ? "ready" : "waiting"}`}>
          <ShieldCheck size={16} /> {health?.status === "ready" ? "System ready" : "Checking system"}
        </div>
      </header>

      <main>
        <section className="hero">
          <div>
            <span className="eyebrow">TEND V3 · 11 DATABASES · 1,210 TASKS</span>
            <h1>Benchmark every method.<br /><em>Watch every decision.</em></h1>
            <p>Run the official baselines and full SAG v3 from one reproducible control plane.</p>
          </div>
          <div className="hero-badge"><FlaskConical size={30} /><strong>110</strong><span>tasks / database</span></div>
        </section>

        {error && <div className="error-banner"><AlertTriangle size={16} />{error}<button onClick={() => setError("")}>×</button></div>}
        {health && !health.provider.ready && (
          <div className="setup-banner">
            <TerminalSquare size={20} />
            <div><strong>Provider setup required</strong><span>Add `OPENAI_API_KEY` to `.env`, then restart the API. Deterministic stub mode is available for plumbing tests.</span></div>
          </div>
        )}

        <section className="status-grid" aria-label="System status">
          <StatusCard icon={<Database size={18} />} label="MongoDB" value={health?.mongodb.available ? `${health.mongodb.database_count} databases` : "Unavailable"} ok={health?.mongodb.available ?? false} />
          <StatusCard icon={<TerminalSquare size={18} />} label="Official TEND" value={health?.upstream.available ? (health.upstream.commit?.slice(0, 8) ?? "Connected") : "Not connected"} ok={health?.upstream.available ?? false} />
          <StatusCard icon={<Server size={18} />} label="Provider" value={health?.provider.model ?? "gpt-5.6-luna"} ok={health?.provider.ready ?? false} />
          <StatusCard icon={<CheckCircle2 size={18} />} label="Dataset" value={health?.dataset.available ? `${health.dataset.task_count.toLocaleString()} tasks` : "Not found"} ok={health?.dataset.available ?? false} />
        </section>

        <div className="mode-tabs" role="tablist" aria-label="Run mode">
          <button className={mode === "benchmark" ? "active" : ""} onClick={() => setMode("benchmark")}><FlaskConical size={17} /> Benchmark suite</button>
          <button className={mode === "query" ? "active" : ""} onClick={() => setMode("query")}><Search size={17} /> User-specified query</button>
        </div>

        <section className="workspace-grid" id="runs">
          <article className="panel configuration-panel">
            <div className="panel-head"><div><span className="step">01</span><h2>Select methods</h2></div><span>{selectedMethods.length} selected</span></div>
            <div className="method-list">
              {catalog?.methods.map((method) => (
                <label key={method.id} className={`method-row ${selectedMethods.includes(method.id) ? "selected" : ""}`}>
                  <input type="checkbox" checked={selectedMethods.includes(method.id)} onChange={() => toggle(method.id, selectedMethods, setSelectedMethods)} />
                  <span className={`family ${method.family}`}>{method.family === "sag" ? "SAG" : "BASE"}</span>
                  <span><strong>{method.title}</strong><small>{method.description}</small></span>
                  <ChevronRight size={16} />
                </label>
              )) ?? <div className="loading-lines">Loading official method catalog…</div>}
            </div>
          </article>

          <div className="right-stack">
            <article className="panel run-panel" id="settings">
              <div className="panel-head"><div><span className="step">02</span><h2>{mode === "benchmark" ? "Configure benchmark" : "Configure query"}</h2></div></div>
              {mode === "benchmark" ? (
                <div className="form-grid">
                  <fieldset><legend>Evaluation tracks</legend>{catalog?.tracks.map((track) => <label className="track-option" key={track.id}><input type="checkbox" checked={selectedTracks.includes(track.id)} onChange={() => toggle(track.id, selectedTracks, setSelectedTracks)} /><span><strong>{track.title}</strong><small>{track.description}</small></span></label>)}</fieldset>
                  <RunSettings health={health} concurrency={concurrency} setConcurrency={setConcurrency} model={model} setModel={setModel} reasoningEffort={reasoningEffort} setReasoningEffort={setReasoningEffort} />
                </div>
              ) : (
                <div className="custom-form">
                  <label className="field"><span>Database</span><select value={database} onChange={(event) => setDatabase(event.target.value)}>{catalog?.dataset.databases?.map((name) => <option key={name}>{name}</option>)}</select></label>
                  <label className="field"><span>Natural-language query</span><textarea rows={5} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a question about the selected MongoDB database…" /></label>
                  <label className="execute-option"><input type="checkbox" checked={executeCustom} onChange={(event) => setExecuteCustom(event.target.checked)} /><span><strong>Execute generated MQL</strong><small>Return a bounded 100-row preview.</small></span></label>
                  <RunSettings health={health} concurrency={concurrency} setConcurrency={setConcurrency} model={model} setModel={setModel} reasoningEffort={reasoningEffort} setReasoningEffort={setReasoningEffort} compact />
                </div>
              )}
            </article>

            <article className="launch-card">
              <div><span>{mode === "benchmark" ? "PLANNED WORKLOAD" : "SELECTED METHODS"}</span><strong>{mode === "benchmark" ? plannedTasks.toLocaleString() : selectedMethods.length}</strong><small>{mode === "benchmark" ? "method × track × benchmark tasks" : "queries will be generated"}</small></div>
              <button disabled={busy || !health?.execution.available || !selectedMethods.length || (mode === "query" && !question.trim())} onClick={startRun}><Play size={18} fill="currentColor" /> {busy ? "Starting…" : mode === "benchmark" ? "Start benchmark" : "Run query"}</button>
            </article>

            <Monitor run={activeRun} items={items} busy={busy} onControl={controlRun} />
          </div>
        </section>

        <ResultsPanel
          run={activeRun}
          results={results}
          onEvaluate={async () => {
            if (!activeRun) return;
            await api.evaluate(activeRun.id);
            await refreshRun(activeRun.id);
          }}
        />

        <section className="panel history-panel">
          <div className="panel-head"><div><span className="step">04</span><h2>Recent runs</h2></div><span>{countLabel(runs.length, "run")}</span></div>
          {runs.length ? <div className="history-table">{runs.map((run) => <button key={run.id} className={activeRun?.id === run.id ? "active" : ""} onClick={() => refreshRun(run.id)}><span><strong>{run.name}</strong><small>{new Date(run.created_at).toLocaleString()} · {run.method_ids.length} methods · {run.total_items.toLocaleString()} tasks</small></span><RunStatusPill status={run.status} /><span className="history-progress">{Math.round(run.progress * 100)}%</span><ChevronRight size={16} /></button>)}</div> : <div className="empty-history">No runs yet. Configure the first benchmark above.</div>}
        </section>
      </main>
    </div>
  );
}

function formatPercent(value: number | undefined) {
  return `${((value ?? 0) * 100).toFixed(1)}%`;
}

function ResultsPanel({ run, results, onEvaluate }: {
  run: RunView | null;
  results: EvaluationResults | null;
  onEvaluate: () => Promise<void>;
}) {
  const [track, setTrack] = useState("canonical");
  const [systemId, setSystemId] = useState("");
  const [axis, setAxis] = useState("domain");
  const [outcome, setOutcome] = useState("");
  const [records, setRecords] = useState<ResultRecordPage | null>(null);
  const [loadingRecords, setLoadingRecords] = useState(false);

  const availableTracks = results?.tracks ?? [];
  const report = results?.reports[track];
  const systems = Object.keys(report?.systems ?? {});
  const axes = Object.keys(report?.system_slice_aggregates?.[systemId] ?? {});
  const sliceBuckets = report?.system_slice_aggregates?.[systemId]?.[axis] ?? {};

  useEffect(() => {
    if (availableTracks.length && !availableTracks.includes(track)) setTrack(availableTracks[0]);
  }, [availableTracks.join("|"), track]);

  useEffect(() => {
    if (systems.length && !systems.includes(systemId)) setSystemId(systems[0]);
  }, [systems.join("|"), systemId]);

  useEffect(() => {
    if (axes.length && !axes.includes(axis)) setAxis(axes[0]);
  }, [axes.join("|"), axis]);

  useEffect(() => {
    if (!run || !report || !systemId) {
      setRecords(null);
      return;
    }
    setLoadingRecords(true);
    api.resultRecords(run.id, {
      track,
      system_id: systemId,
      outcome: outcome || undefined,
      limit: 50
    }).then(setRecords).catch(() => setRecords(null)).finally(() => setLoadingRecords(false));
  }, [run?.id, track, systemId, outcome, report]);

  if (!run || run.mode !== "benchmark") {
    return (
      <section className="panel results-panel" id="results">
        <div className="panel-head"><div><span className="step">05</span><h2>Benchmark results</h2></div><span>No benchmark selected</span></div>
        <div className="empty-monitor"><BarChart3 size={26} /><strong>Metrics appear automatically</strong><p>Select or start a benchmark run to inspect EXC, EXF1, claim-axis slices, outcomes, and record-level diagnostics.</p></div>
      </section>
    );
  }

  if (run.status !== "completed") {
    return (
      <section className="panel results-panel" id="results">
        <div className="panel-head"><div><span className="step">05</span><h2>Benchmark results</h2></div><RunStatusPill status={run.status} /></div>
        <div className="evaluation-wait">
          <AlertTriangle size={24} />
          <div><strong>Scoring is withheld until every MQL is accepted</strong><p>Rejected generations are regenerated automatically. Cancelled or failed runs are never evaluated as partial benchmarks.</p></div>
        </div>
      </section>
    );
  }

  if (!results || results.status !== "completed" || !report) {
    return (
      <section className="panel results-panel" id="results">
        <div className="panel-head"><div><span className="step">05</span><h2>Benchmark results</h2></div><RunStatusPill status={results?.status ?? "pending"} /></div>
        <div className="evaluation-wait">
          {results?.status === "failed" ? <AlertTriangle size={24} /> : <Activity size={24} />}
          <div><strong>{results?.status === "failed" ? "Evaluation needs attention" : "Official TEND evaluation is queued"}</strong><p>{results?.error ?? "Scoring starts automatically when generation reaches a terminal state."}</p></div>
          {results?.status === "failed" && <button onClick={() => void onEvaluate()}><RotateCw size={14} /> Retry evaluation</button>}
        </div>
      </section>
    );
  }

  const selectedSystem = report.systems[systemId];
  return (
    <section className="panel results-panel" id="results">
      <div className="panel-head results-head">
        <div><span className="step">05</span><h2>Benchmark results</h2></div>
        <div className="result-actions">
          <RunStatusPill status={report.status} />
          <button onClick={() => void onEvaluate()}><RotateCw size={13} /> Re-evaluate</button>
        </div>
      </div>
      <div className="result-trackbar">
        <div>{availableTracks.map((value) => <button key={value} className={track === value ? "active" : ""} onClick={() => setTrack(value)}>{value}</button>)}</div>
        <span>{report.release_record_count.toLocaleString()} release records / method</span>
      </div>

      <div className="result-section">
        <div className="result-title"><div><BarChart3 size={16} /><strong>Method leaderboard</strong></div><span>Official TEND scores</span></div>
        <div className="metric-table-wrap"><table className="metric-table"><thead><tr><th>Method</th><th>Records</th><th>EXC</th><th>EXF1</th><th>Correct</th><th>No submission</th><th>Invalid</th><th>Exec error</th></tr></thead><tbody>
          {Object.entries(report.systems).map(([id, value]) => <tr key={id} className={id === systemId ? "selected" : ""} onClick={() => setSystemId(id)}><td><strong>{id}</strong></td><td>{value.record_count.toLocaleString()}</td><td className="metric-primary">{formatPercent(value.scores.EXC)}</td><td>{formatPercent(value.scores.EXF1)}</td><td>{value.outcome_distribution.counts.correct ?? 0}</td><td>{value.outcome_distribution.counts.no_submission ?? 0}</td><td>{value.outcome_distribution.counts.invalid ?? 0}</td><td>{value.outcome_distribution.counts.exec_error ?? 0}</td></tr>)}
        </tbody></table></div>
      </div>

      <div className="result-two-column">
        <div className="result-section">
          <div className="result-title"><div><FlaskConical size={16} /><strong>Claim-axis slices</strong></div><select value={axis} onChange={(event) => setAxis(event.target.value)}>{axes.map((value) => <option key={value}>{value}</option>)}</select></div>
          <div className="metric-table-wrap compact-table"><table className="metric-table"><thead><tr><th>{axis.replaceAll("_", " ")}</th><th>N</th><th>EXC</th><th>EXF1</th></tr></thead><tbody>{Object.entries(sliceBuckets).map(([value, bucket]) => <tr key={value}><td><strong>{value}</strong></td><td>{bucket.record_count}</td><td className="metric-primary">{formatPercent(bucket.scores.EXC)}</td><td>{formatPercent(bucket.scores.EXF1)}</td></tr>)}</tbody></table></div>
        </div>
        <div className="result-section">
          <div className="result-title"><div><CircleDot size={16} /><strong>Outcome decomposition</strong></div><span>{systemId}</span></div>
          <div className="outcome-grid">{report.outcome_buckets_order.map((bucket) => <button key={bucket} className={outcome === bucket ? "active" : ""} onClick={() => setOutcome(outcome === bucket ? "" : bucket)}><span>{bucket.replaceAll("_", " ")}</span><strong>{selectedSystem?.outcome_distribution.counts[bucket] ?? 0}</strong><small>{formatPercent(selectedSystem?.outcome_distribution.fractions[bucket])}</small></button>)}</div>
        </div>
      </div>

      <div className="result-section">
        <div className="result-title"><div><FileSearch size={16} /><strong>Record drill-down</strong></div><span>{loadingRecords ? "Loading..." : `${records?.total ?? 0} matching records`}</span></div>
        <div className="record-list">{records?.items.map((row) => {
          const diagnostics = row.diagnostics ?? {};
          const errorCode = typeof diagnostics.error_code === "string" ? diagnostics.error_code : "";
          return <div className="record-row" key={`${row.system_id}-${row.db_id}-${row.record_id}`}><span><strong>{row.db_id} / {row.record_id}</strong><small>{errorCode || row.status}</small></span><RunStatusPill status={row.outcome} /><b>EXC {formatPercent(row.metrics.EXC)}</b><b>EXF1 {formatPercent(row.metrics.EXF1)}</b></div>;
        }) ?? null}</div>
      </div>

      <div className="export-bar"><span><Download size={15} /> Export {track}</span>{(["report_json", "report_md", "per_record_csv", "per_record_jsonl"] as const).map((kind) => <a key={kind} href={`/api/runs/${run.id}/results/export/${track}/${kind}`}>{kind.replaceAll("_", " ")}</a>)}</div>
    </section>
  );
}

function RunSettings({
  health,
  concurrency,
  setConcurrency,
  model,
  setModel,
  reasoningEffort,
  setReasoningEffort,
  compact = false
}: {
  health: Health | null;
  concurrency: number;
  setConcurrency: (value: number) => void;
  model: string;
  setModel: (value: string) => void;
  reasoningEffort: string;
  setReasoningEffort: (value: string) => void;
  compact?: boolean;
}) {
  return (
    <div className={compact ? "compact-settings" : "settings-fields"}>
      <label className="field"><span>Worker concurrency</span><input type="number" min="1" max="128" value={concurrency || ""} onChange={(event) => setConcurrency(Number(event.target.value))} /><small>Server default {health?.defaults.concurrency ?? 4} · adjustable per run</small></label>
      <label className="field"><span>Provider / model</span><input value={model} onChange={(event) => setModel(event.target.value)} /><small>One model shared by all selected methods</small></label>
      {!compact && <label className="field"><span>Reasoning effort</span><select value={reasoningEffort} onChange={(event) => setReasoningEffort(event.target.value)}>{["none", "low", "medium", "high", "xhigh", "max"].map((value) => <option key={value}>{value}</option>)}</select><small>Configured once at run level</small></label>}
    </div>
  );
}

function Monitor({ run, items, busy, onControl }: {
  run: RunView | null;
  items: WorkItem[];
  busy: boolean;
  onControl: (action: "pause" | "resume" | "cancel") => void;
}) {
  if (!run) {
    return <article className="panel monitor-panel"><div className="panel-head"><div><span className="step">03</span><h2>Live monitor</h2></div><span className="muted">No active run</span></div><div className="empty-monitor"><Activity size={26} /><strong>Ready for orchestration</strong><p>Progress, pause, resume, cancellation, ETA, and worker events will appear here.</p></div></article>;
  }
  const finished = run.succeeded_items + run.failed_items + run.cancelled_items;
  return (
    <article className="panel monitor-panel active-monitor">
      <div className="panel-head"><div><span className="step">03</span><h2>{run.name}</h2></div><RunStatusPill status={run.status} /></div>
      <div className="monitor-body">
        <div className="progress-head"><div><strong>{Math.round(run.progress * 100)}%</strong><span>{finished.toLocaleString()} / {run.total_items.toLocaleString()} tasks</span></div><div className="run-actions">{run.status === "running" && <button disabled={busy} onClick={() => onControl("pause")}><Pause size={14} /> Pause</button>}{run.status === "paused" && <button disabled={busy} onClick={() => onControl("resume")}><Play size={14} /> Resume</button>}{!terminalStatuses.has(run.status) && <button className="danger" disabled={busy} onClick={() => onControl("cancel")}><Square size={13} /> Cancel</button>}</div></div>
        <div className="progress-track"><div style={{ width: `${run.progress * 100}%` }} /></div>
        <div className="counter-grid"><span><i className="dot running" />Running<strong>{run.running_items}</strong></span><span><i className="dot retrying" />Retrying<strong>{run.retrying_items}</strong></span><span><i className="dot pending" />Pending<strong>{run.pending_items}</strong></span><span><i className="dot success" />Accepted<strong>{run.succeeded_items}</strong></span><span><i className="dot failure" />Failed<strong>{run.failed_items}</strong></span></div>
        {run.retrying_items > 0 && <p className="retry-notice">Rejected generations are being regenerated automatically. Only accepted MQL has passed parsing and MongoDB execution.</p>}
        <div className="work-feed"><div className="feed-head"><span>Recent work items</span><small>{run.model} · {run.reasoning_effort} · C{run.concurrency}</small></div>{items.slice(0, 6).map((item) => <div className="work-row" key={item.id}>{item.status === "succeeded" ? <CheckCircle2 size={14} /> : item.status === "failed" ? <XCircle size={14} /> : <Activity size={14} />}<span><strong>{item.method_id}</strong><small>{item.db_id} · {item.track} · {item.record_id ?? "custom"}</small></span><RunStatusPill status={item.status} /></div>)}</div>
        {run.mode === "custom_query" && <CustomQueryOutputs items={items} />}
      </div>
    </article>
  );
}

function CustomQueryOutputs({ items }: { items: WorkItem[] }) {
  const completed = items.filter((item) => item.result || item.error);
  if (!completed.length) return null;
  return <div className="custom-results"><div className="feed-head"><span>Generated query outputs</span><small>Execution previews are capped at 100 rows</small></div>{completed.map((item) => {
    const result = item.result ?? {};
    const mql = typeof result.MQL === "string" ? result.MQL : "No MQL was returned.";
    const preview = result.execution_preview as { ok?: boolean; rows?: unknown[]; error?: string } | undefined;
    return <details key={item.id} open={completed.length === 1}><summary><span><strong>{item.method_id}</strong><small>{preview?.ok ? `${preview.rows?.length ?? 0} preview rows` : item.error || preview?.error || item.status}</small></span><RunStatusPill status={item.status} /></summary><pre>{mql}</pre>{preview && <div className={`preview-box ${preview.ok ? "ok" : "failed"}`}><strong>{preview.ok ? "Execution preview" : "Execution error"}</strong><code>{preview.ok ? JSON.stringify((preview.rows ?? []).slice(0, 3), null, 2) : preview.error}</code></div>}</details>;
  })}</div>;
}
