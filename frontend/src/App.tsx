import {
  Activity,
  BarChart3,
  Beaker,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  Database,
  FlaskConical,
  Play,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  TerminalSquare
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Catalog, Health } from "./types";

type Mode = "benchmark" | "query";

function StatusCard({
  icon,
  label,
  value,
  ok
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  ok: boolean;
}) {
  return (
    <article className="status-card">
      <div className={`status-icon ${ok ? "ok" : "warn"}`}>{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <CircleDot className={ok ? "signal-ok" : "signal-warn"} size={14} />
    </article>
  );
}

export default function App() {
  const [mode, setMode] = useState<Mode>("benchmark");
  const [health, setHealth] = useState<Health | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selectedMethods, setSelectedMethods] = useState<string[]>(["direct", "sag_v3"]);
  const [selectedTracks, setSelectedTracks] = useState<string[]>(["canonical"]);
  const [concurrency, setConcurrency] = useState(4);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.health(), api.catalog()])
      .then(([healthValue, catalogValue]) => {
        setHealth(healthValue);
        setCatalog(catalogValue);
        setConcurrency(healthValue.defaults.concurrency);
      })
      .catch((value) => setError(value instanceof Error ? value.message : String(value)));
  }, []);

  const plannedTasks = useMemo(() => {
    const perTrack = catalog?.dataset.task_count ?? 1210;
    return selectedMethods.length * selectedTracks.length * perTrack;
  }, [catalog, selectedMethods.length, selectedTracks.length]);

  const toggle = (id: string, current: string[], update: (next: string[]) => void) => {
    update(current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
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

        {error && <div className="error-banner">{error}. Start the API to load live status.</div>}

        <section className="status-grid" aria-label="System status">
          <StatusCard icon={<Database size={18} />} label="MongoDB" value={health?.mongodb.available ? `${health.mongodb.database_count} databases` : "Unavailable"} ok={health?.mongodb.available ?? false} />
          <StatusCard icon={<TerminalSquare size={18} />} label="Official TEND" value={health?.upstream.commit?.slice(0, 8) ?? "Not connected"} ok={health?.upstream.available ?? false} />
          <StatusCard icon={<Server size={18} />} label="Provider" value={health?.provider.model ?? "gpt-5.6-luna"} ok={health?.provider.configured ?? false} />
          <StatusCard icon={<CheckCircle2 size={18} />} label="Dataset" value={health?.dataset.available ? `${health.dataset.task_count.toLocaleString()} tasks` : "Not found"} ok={health?.dataset.available ?? false} />
        </section>

        <div className="mode-tabs" role="tablist" aria-label="Run mode">
          <button className={mode === "benchmark" ? "active" : ""} onClick={() => setMode("benchmark")}><FlaskConical size={17} /> Benchmark suite</button>
          <button className={mode === "query" ? "active" : ""} onClick={() => setMode("query")}><Search size={17} /> User-specified query</button>
        </div>

        {mode === "benchmark" ? (
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
                <div className="panel-head"><div><span className="step">02</span><h2>Configure run</h2></div></div>
                <div className="form-grid">
                  <fieldset><legend>Evaluation tracks</legend>{catalog?.tracks.map((track) => <label className="track-option" key={track.id}><input type="checkbox" checked={selectedTracks.includes(track.id)} onChange={() => toggle(track.id, selectedTracks, setSelectedTracks)} /><span><strong>{track.title}</strong><small>{track.description}</small></span></label>)}</fieldset>
                  <label className="field"><span>Worker concurrency</span><input type="number" min="1" max="128" value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} /><small>Default 4 · adjustable per run</small></label>
                  <label className="field"><span>Provider / model</span><input value={health?.provider.model ?? "gpt-5.6-luna"} readOnly /><small>One model shared by all methods</small></label>
                  <label className="field"><span>Reasoning effort</span><input value={health?.provider.reasoning_effort ?? "medium"} readOnly /><small>Configured at run level</small></label>
                </div>
              </article>

              <article className="launch-card">
                <div><span>PLANNED WORKLOAD</span><strong>{plannedTasks.toLocaleString()}</strong><small>method × track × benchmark tasks</small></div>
                <button disabled title="Run orchestration arrives in the next milestone"><Play size={18} fill="currentColor" /> Start benchmark</button>
              </article>

              <article className="panel monitor-panel">
                <div className="panel-head"><div><span className="step">03</span><h2>Live monitor</h2></div><span className="muted">No active run</span></div>
                <div className="empty-monitor"><Activity size={26} /><strong>Ready for orchestration</strong><p>Progress, pause, resume, cancellation, ETA, and worker events will appear here.</p></div>
              </article>
            </div>
          </section>
        ) : (
          <section className="panel query-panel">
            <div className="panel-head"><div><span className="step">01</span><h2>Test a custom question</h2></div></div>
            <div className="query-form"><label className="field"><span>Database</span><select>{catalog?.dataset.databases?.map((database) => <option key={database}>{database}</option>)}</select></label><label className="field grow"><span>Natural-language query</span><textarea placeholder="Ask a question about the selected MongoDB database…" rows={5} /></label><button disabled><Play size={18} /> Run selected methods</button></div>
          </section>
        )}
      </main>
    </div>
  );
}

