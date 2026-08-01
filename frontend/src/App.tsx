import {
  Activity,
  AlertTriangle,
  BarChart3,
  Beaker,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Database,
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
import type { Catalog, Health, RunView, WorkItem } from "./types";

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
  const [selectedMethods, setSelectedMethods] = useState<string[]>(["direct", "sag_v3"]);
  const [selectedTracks, setSelectedTracks] = useState<string[]>(["canonical"]);
  const [concurrency, setConcurrency] = useState(4);
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
    setDatabase((current) => current || catalogValue.dataset.databases?.[0] || "");
  }, []);

  const refreshRun = useCallback(async (runId: string) => {
    const [run, workItems] = await Promise.all([api.run(runId), api.items(runId, 100)]);
    setActiveRun(run);
    setItems(workItems);
    setRuns((current) => [run, ...current.filter((item) => item.id !== run.id)]);
    return run;
  }, []);

  useEffect(() => {
    refreshSystem().catch((value) => setError(String(value)));
  }, [refreshSystem]);

  useEffect(() => {
    if (!activeRun || terminalStatuses.has(activeRun.status)) return;
    const interval = window.setInterval(() => {
      refreshRun(activeRun.id).catch((value) => setError(String(value)));
    }, 900);
    return () => window.clearInterval(interval);
  }, [activeRun?.id, activeRun?.status, refreshRun]);

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
            concurrency
          })
        : await api.createRun({
            mode: "custom_query",
            method_ids: selectedMethods,
            database_id: database,
            question,
            execute_custom_query: executeCustom,
            concurrency
          });
      setActiveRun(run);
      setItems([]);
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
          <button className="active"><Activity size={16} /> Runs</button>
          <button><BarChart3 size={16} /> Results</button>
          <button><Settings2 size={16} /> Settings</button>
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
          <StatusCard icon={<TerminalSquare size={18} />} label="Official TEND" value={health?.upstream.commit?.slice(0, 8) ?? "Not connected"} ok={health?.upstream.available ?? false} />
          <StatusCard icon={<Server size={18} />} label="Provider" value={health?.provider.model ?? "gpt-5.6-luna"} ok={health?.provider.ready ?? false} />
          <StatusCard icon={<CheckCircle2 size={18} />} label="Dataset" value={health?.dataset.available ? `${health.dataset.task_count.toLocaleString()} tasks` : "Not found"} ok={health?.dataset.available ?? false} />
        </section>

        <div className="mode-tabs" role="tablist" aria-label="Run mode">
          <button className={mode === "benchmark" ? "active" : ""} onClick={() => setMode("benchmark")}><FlaskConical size={17} /> Benchmark suite</button>
          <button className={mode === "query" ? "active" : ""} onClick={() => setMode("query")}><Search size={17} /> User-specified query</button>
        </div>

        <section className="workspace-grid">
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
            <article className="panel run-panel">
              <div className="panel-head"><div><span className="step">02</span><h2>{mode === "benchmark" ? "Configure benchmark" : "Configure query"}</h2></div></div>
              {mode === "benchmark" ? (
                <div className="form-grid">
                  <fieldset><legend>Evaluation tracks</legend>{catalog?.tracks.map((track) => <label className="track-option" key={track.id}><input type="checkbox" checked={selectedTracks.includes(track.id)} onChange={() => toggle(track.id, selectedTracks, setSelectedTracks)} /><span><strong>{track.title}</strong><small>{track.description}</small></span></label>)}</fieldset>
                  <RunSettings health={health} concurrency={concurrency} setConcurrency={setConcurrency} />
                </div>
              ) : (
                <div className="custom-form">
                  <label className="field"><span>Database</span><select value={database} onChange={(event) => setDatabase(event.target.value)}>{catalog?.dataset.databases?.map((name) => <option key={name}>{name}</option>)}</select></label>
                  <label className="field"><span>Natural-language query</span><textarea rows={5} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a question about the selected MongoDB database…" /></label>
                  <label className="execute-option"><input type="checkbox" checked={executeCustom} onChange={(event) => setExecuteCustom(event.target.checked)} /><span><strong>Execute generated MQL</strong><small>Return a bounded 100-row preview.</small></span></label>
                  <RunSettings health={health} concurrency={concurrency} setConcurrency={setConcurrency} compact />
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

        <section className="panel history-panel">
          <div className="panel-head"><div><span className="step">04</span><h2>Recent runs</h2></div><span>{countLabel(runs.length, "run")}</span></div>
          {runs.length ? <div className="history-table">{runs.map((run) => <button key={run.id} className={activeRun?.id === run.id ? "active" : ""} onClick={() => refreshRun(run.id)}><span><strong>{run.name}</strong><small>{new Date(run.created_at).toLocaleString()} · {run.method_ids.length} methods · {run.total_items.toLocaleString()} tasks</small></span><RunStatusPill status={run.status} /><span className="history-progress">{Math.round(run.progress * 100)}%</span><ChevronRight size={16} /></button>)}</div> : <div className="empty-history">No runs yet. Configure the first benchmark above.</div>}
        </section>
      </main>
    </div>
  );
}

function RunSettings({ health, concurrency, setConcurrency, compact = false }: {
  health: Health | null;
  concurrency: number;
  setConcurrency: (value: number) => void;
  compact?: boolean;
}) {
  return (
    <div className={compact ? "compact-settings" : "settings-fields"}>
      <label className="field"><span>Worker concurrency</span><input type="number" min="1" max="128" value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /><small>Default 4 · adjustable per run</small></label>
      <label className="field"><span>Provider / model</span><input value={health?.provider.model ?? "gpt-5.6-luna"} readOnly /><small>Shared by all selected methods</small></label>
      {!compact && <label className="field"><span>Reasoning effort</span><input value={health?.provider.reasoning_effort ?? "medium"} readOnly /><small>Configured at run level</small></label>}
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
        <div className="counter-grid"><span><i className="dot running" />Running<strong>{run.running_items}</strong></span><span><i className="dot pending" />Pending<strong>{run.pending_items}</strong></span><span><i className="dot success" />Succeeded<strong>{run.succeeded_items}</strong></span><span><i className="dot failure" />Failed<strong>{run.failed_items}</strong></span></div>
        <div className="work-feed"><div className="feed-head"><span>Recent work items</span><small>{run.model} · {run.reasoning_effort} · C{run.concurrency}</small></div>{items.slice(-6).reverse().map((item) => <div className="work-row" key={item.id}>{item.status === "succeeded" ? <CheckCircle2 size={14} /> : item.status === "failed" ? <XCircle size={14} /> : <Activity size={14} />}<span><strong>{item.method_id}</strong><small>{item.db_id} · {item.track} · {item.record_id ?? "custom"}</small></span><RunStatusPill status={item.status} /></div>)}</div>
      </div>
    </article>
  );
}
