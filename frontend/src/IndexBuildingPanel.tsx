import {
  AlertTriangle,
  Boxes,
  Check,
  CircleDashed,
  Clock3,
  Database,
  FileJson2,
  LoaderCircle,
  Pause,
  Play,
  RotateCw,
  Square,
  X
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Health, IndexStageTrace, SchemaIndexRunView } from "./types";

const ACTIVE_KEY = "tend-new-methods-index-run";
const INDEX_LABELS: Record<string, string> = {
  schema_traversal: "Full schema traversal",
  value_profiling: "Exact value profiling",
  reference_inference: "Reference inference",
  embedding_generation: "Embedding generation",
  artifact_validation: "Artifact validation"
};

const terminal = new Set(["completed", "failed", "cancelled", "paused"]);

export function IndexBuildingPanel({ health }: { health: Health | null }) {
  const [databases, setDatabases] = useState<string[]>([]);
  const [database, setDatabase] = useState("");
  const [runs, setRuns] = useState<SchemaIndexRunView[]>([]);
  const [active, setActive] = useState<SchemaIndexRunView | null>(null);
  const [selectedStage, setSelectedStage] = useState("schema_traversal");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [databaseValues, runValues] = await Promise.all([
      api.schemaIndexDatabases(),
      api.schemaIndexRuns()
    ]);
    setDatabases(databaseValues);
    setDatabase((current) => current || databaseValues[0] || "");
    setRuns(runValues);
    setActive((current) => {
      const wanted = current?.run_id ?? window.localStorage.getItem(ACTIVE_KEY);
      const selected = runValues.find((item) => item.run_id === wanted) ?? runValues[0] ?? null;
      if (selected) {
        window.localStorage.setItem(ACTIVE_KEY, selected.run_id);
        setSelectedStage(currentStage(selected));
      }
      return selected;
    });
  }, []);

  useEffect(() => {
    void refresh().catch((value) => setError(message(value)));
  }, [refresh]);

  useEffect(() => {
    if (!active || terminal.has(active.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const updated = await api.schemaIndexRun(active.run_id);
        setActive(updated);
        setRuns((current) => [updated, ...current.filter((item) => item.run_id !== updated.run_id)]);
        setSelectedStage(currentStage(updated));
      } catch (value) {
        setError(message(value));
      }
    }, 900);
    return () => window.clearInterval(timer);
  }, [active?.run_id, active?.status]);

  const create = async () => {
    if (!database) return;
    setBusy(true);
    setError("");
    try {
      const run = await api.createSchemaIndexRun(database);
      selectLocal(run);
      setRuns((current) => [run, ...current]);
    } catch (value) {
      setError(message(value));
    } finally {
      setBusy(false);
    }
  };

  const control = async (action: "pause" | "resume" | "cancel") => {
    if (!active) return;
    setBusy(true);
    try {
      const updated = action === "pause"
        ? await api.pauseSchemaIndexRun(active.run_id)
        : action === "resume"
          ? await api.resumeSchemaIndexRun(active.run_id)
          : await api.cancelSchemaIndexRun(active.run_id);
      selectLocal(updated);
      setRuns((current) => [updated, ...current.filter((item) => item.run_id !== updated.run_id)]);
    } catch (value) {
      setError(message(value));
    } finally {
      setBusy(false);
    }
  };

  const selectLocal = (run: SchemaIndexRunView) => {
    setActive(run);
    window.localStorage.setItem(ACTIVE_KEY, run.run_id);
    setSelectedStage(currentStage(run));
  };

  const progress = useMemo(() => {
    if (!active?.stages.length) return 0;
    return Math.round(active.stages.reduce((sum, stage) => sum + stage.progress, 0) / active.stages.length * 100);
  }, [active]);
  const trace = active?.stages.find((stage) => stage.stage === selectedStage);

  return <main className="anchor-main nm-main">
    <section className="anchor-intro">
      <div><span className="eyebrow">NEW METHOD / OFFLINE PREPROCESSING</span><h1>Build the evidence index.<br /><em>From every schema path.</em></h1><p>Traverse the complete schema, scan exact MongoDB values, infer strict references, and validate every embedding artifact.</p></div>
      <div className="anchor-boundary"><Database size={25} /><strong>{databases.length}</strong><span>schema databases</span></div>
    </section>
    {error && <div className="error-banner"><AlertTriangle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
    <section className="anchor-layout">
      <aside className="panel anchor-history">
        <div className="panel-head"><div><span className="step">H</span><h2>Index builds</h2></div><button className="anchor-icon-button" onClick={() => void refresh()} title="Refresh"><RotateCw size={15} /></button></div>
        <div className="anchor-boundary-note"><Boxes size={15} /><span>A completed build is exposed only after every artifact passes validation.</span></div>
        <div className="anchor-history-list">{runs.map((run) => <button key={run.run_id} className={active?.run_id === run.run_id ? "active" : ""} onClick={() => selectLocal(run)}><span className={`run-status status-${run.status}`}><i />{run.status}</span><small>{relativeTime(run.updated_at)}</small><strong>{run.database_id}</strong><span>{run.node_count.toLocaleString()} nodes / {run.value_count.toLocaleString()} values</span></button>)}{!runs.length && <div className="empty-history">No index builds yet.</div>}</div>
      </aside>
      <div className="anchor-content">
        <section className="panel nm-composer">
          <div className="panel-head"><div><span className="step">01</span><h2>Build a database index</h2></div><span className={`nm-readiness ${health?.embedding_provider?.ready ? "ready" : "waiting"}`}>{health?.embedding_provider?.ready ? health.embedding_provider.model : "Embedding key required"}</span></div>
          <div className="nm-form-row"><label><span>Full schema tree</span><select value={database} onChange={(event) => setDatabase(event.target.value)}>{databases.map((item) => <option key={item} value={item}>{item}</option>)}</select></label><button disabled={busy || !database || !health?.embedding_provider?.ready} onClick={() => void create()}>{busy ? <LoaderCircle size={16} className="spin" /> : <Database size={16} />} Build index</button></div>
          <div className="anchor-composer-foot"><span>MongoDB exact-value scan / strict 90% reference inference / resumable embedding checkpoints</span><span>{health?.mongodb.available ? "MongoDB connected" : "MongoDB unavailable"}</span></div>
        </section>
        {active ? <section className="panel anchor-monitor">
          <div className="anchor-run-head"><div><span className="eyebrow">INDEX {active.index_id.slice(0, 8)} / {active.embedding_model}</span><h2>{active.database_id}</h2></div><div className="nm-run-actions">{["running", "pausing"].includes(active.status) && <button disabled={busy} onClick={() => void control("pause")}><Pause size={14} /> Pause</button>}{["paused", "failed", "created"].includes(active.status) && <button disabled={busy} onClick={() => void control("resume")}><Play size={14} /> Resume</button>}{!["completed", "cancelled"].includes(active.status) && <button className="danger" disabled={busy} onClick={() => void control("cancel")}><Square size={13} /> Cancel</button>}<div className="anchor-progress"><span className={`run-status status-${active.status}`}><i />{active.status}</span><strong>{progress}%</strong><div><i style={{ width: `${progress}%` }} /></div></div></div></div>
          {active.failure && <div className="error-banner embedded"><AlertTriangle size={15} />{active.failure}</div>}
          <div className="nm-metrics"><Metric label="Schema nodes" value={active.node_count} /><Metric label="Exact values" value={active.value_count} /><Metric label="Dynamic keys" value={active.dynamic_key_count} /><Metric label="Array paths" value={active.array_path_count} /><Metric label="References" value={active.reference_edge_count} /><Metric label="Embeddings" value={active.embedding_count} /></div>
          <div className="anchor-stage-grid"><IndexTimeline stages={active.stages} selected={selectedStage} onSelect={setSelectedStage} /><article className="anchor-artifact"><header><div><span className="eyebrow">BUILD CHECKPOINT</span><strong>{INDEX_LABELS[selectedStage] ?? selectedStage}</strong></div><span><Clock3 size={12} />{Math.round((trace?.progress ?? 0) * 100)}%</span></header><div className="anchor-artifact-scroll"><IndexArtifact trace={trace} /></div></article></div>
        </section> : <section className="panel anchor-empty"><Database size={34} /><span className="eyebrow">INDEX BEFORE RETRIEVAL</span><h2>Select one of the 11 migrated schema trees.</h2><p>The backend stores validated nodes, exact membership sets, reference edges, and embeddings under its runtime index folder.</p></section>}
      </div>
    </section>
  </main>;
}

function IndexTimeline({ stages, selected, onSelect }: { stages: IndexStageTrace[]; selected: string; onSelect: (stage: string) => void }) {
  return <aside className="anchor-timeline"><div className="anchor-timeline-title"><Database size={13} /> Index trace</div>{stages.map((stage, index) => <button key={stage.stage} className={`${stage.status} ${selected === stage.stage ? "selected" : ""}`} onClick={() => onSelect(stage.stage)}><span className="anchor-stage-icon">{stage.status === "completed" ? <Check size={13} /> : stage.status === "running" ? <LoaderCircle size={13} className="spin" /> : stage.status === "failed" ? <X size={13} /> : stage.status === "paused" ? <Pause size={13} /> : <CircleDashed size={13} />}</span><span><strong>{INDEX_LABELS[stage.stage] ?? stage.stage}</strong><small>{stage.summary || "Waiting"}</small></span><i>{Math.round(stage.progress * 100)}%</i>{index < stages.length - 1 && <b />}</button>)}</aside>;
}

function IndexArtifact({ trace }: { trace?: IndexStageTrace }) {
  if (!trace || ["pending", "running", "paused"].includes(trace.status)) return <div className="anchor-waiting"><Clock3 size={25} /><h3>{trace ? INDEX_LABELS[trace.stage] : "Stage"}</h3><p>{trace?.summary || "This stage has not started."}</p>{trace && <div className="nm-stage-progress"><i style={{ width: `${trace.progress * 100}%` }} /></div>}</div>;
  return <div className="anchor-stack"><section className="anchor-artifact-hero"><span>{trace.status === "completed" ? <Check size={20} /> : <AlertTriangle size={20} />}</span><div><small className="eyebrow">{trace.status}</small><h2>{trace.summary}</h2><p>{trace.error || "This verified checkpoint is persisted and can be inspected after refresh."}</p></div></section><details className="anchor-raw" open><summary><FileJson2 size={13} /> Stage artifact</summary><pre>{JSON.stringify(trace.artifact, null, 2)}</pre></details></div>;
}

function Metric({ label, value }: { label: string; value: number }) {
  return <div><span>{label}</span><strong>{value.toLocaleString()}</strong></div>;
}

function currentStage(run: SchemaIndexRunView) {
  return run.stages.find((item) => ["running", "paused", "failed"].includes(item.status))?.stage
    ?? [...run.stages].reverse().find((item) => item.status === "completed")?.stage
    ?? "schema_traversal";
}

function relativeTime(value: string) {
  const minutes = Math.floor((Date.now() - new Date(value).getTime()) / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours}h` : `${Math.floor(hours / 24)}d`;
}

function message(value: unknown) {
  return value instanceof Error ? value.message : String(value);
}
