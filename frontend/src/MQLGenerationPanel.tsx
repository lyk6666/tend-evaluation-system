import {
  AlertTriangle,
  Check,
  CircleDashed,
  Clock3,
  Code2,
  DatabaseZap,
  FileJson2,
  Gauge,
  GitBranch,
  LoaderCircle,
  RotateCw,
  Sparkles,
  Wrench,
  X
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type {
  MQLCandidate,
  MQLGenerationRunView,
  MQLValidationIssue,
  PruningStageTrace,
  SchemaPruningRunView
} from "./types";

const ACTIVE_KEY = "tend-new-methods-mql-run";
const LABELS: Record<string, string> = {
  input_preparation: "Question + pruned schemas",
  candidate_generation: "MQL candidates",
  deterministic_validation: "Validation and Mongo probe",
  repair: "Bounded repair",
  execution_selection: "Execution and selection",
  tend_evaluation: "Final result and TEND metrics"
};
const terminal = new Set(["completed", "failed"]);

export function MQLGenerationPanel() {
  const [pruningRuns, setPruningRuns] = useState<SchemaPruningRunView[]>([]);
  const [runs, setRuns] = useState<MQLGenerationRunView[]>([]);
  const [pruningId, setPruningId] = useState("");
  const [ranks, setRanks] = useState<number[]>([]);
  const [repairAttempts, setRepairAttempts] = useState(1);
  const [previewLimit, setPreviewLimit] = useState(100);
  const [active, setActive] = useState<MQLGenerationRunView | null>(null);
  const [selectedStage, setSelectedStage] = useState("input_preparation");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [pruningValues, runValues] = await Promise.all([
      api.schemaPruningRuns(200),
      api.mqlGenerationRuns(100)
    ]);
    const complete = pruningValues.filter((item) => item.status === "completed" && item.pruned_schemas.length);
    setPruningRuns(complete);
    setPruningId((current) => current || complete[0]?.run_id || "");
    setRanks((current) => current.length ? current : complete[0]?.pruned_schemas.map((item) => item.combination_rank) ?? []);
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
        const updated = await api.mqlGenerationRun(active.run_id);
        selectLocal(updated);
        setRuns((current) => [updated, ...current.filter((item) => item.run_id !== updated.run_id)]);
      } catch (value) {
        setError(message(value));
      }
    }, 850);
    return () => window.clearInterval(timer);
  }, [active?.run_id, active?.status]);

  const selectedPruning = pruningRuns.find((item) => item.run_id === pruningId);
  const choosePruning = (runId: string) => {
    setPruningId(runId);
    const run = pruningRuns.find((item) => item.run_id === runId);
    setRanks(run?.pruned_schemas.map((item) => item.combination_rank) ?? []);
  };
  const toggleRank = (rank: number) => setRanks((current) => current.includes(rank)
    ? current.filter((item) => item !== rank)
    : [...current, rank].sort((left, right) => left - right));

  const start = async () => {
    if (!pruningId || !ranks.length) return;
    setBusy(true);
    setError("");
    try {
      const run = await api.createMQLGenerationRun({
        pruning_run_id: pruningId,
        config: {
          combination_ranks: ranks,
          max_repair_attempts: repairAttempts,
          preview_limit: previewLimit
        }
      });
      selectLocal(run);
      setRuns((current) => [run, ...current]);
    } catch (value) {
      setError(message(value));
    } finally {
      setBusy(false);
    }
  };

  const selectLocal = (run: MQLGenerationRunView) => {
    setActive(run);
    window.localStorage.setItem(ACTIVE_KEY, run.run_id);
    setSelectedStage(currentStage(run));
  };
  const progress = useMemo(() => {
    if (!active?.stages.length) return 0;
    return Math.round(active.stages.reduce((sum, stage) => sum + stage.progress, 0) / active.stages.length * 100);
  }, [active]);
  const trace = active?.stages.find((item) => item.stage === selectedStage);

  return <main className="anchor-main nm-main">
    <section className="anchor-intro"><div><span className="eyebrow">NEW METHOD / EXECUTABLE QUERY GENERATION</span><h1>From pruned evidence.<br /><em>Directly to MQL.</em></h1><p>Generate one pipeline per schema alternative, reject unsafe paths, repair bounded failures, and execute every valid candidate.</p></div><div className="anchor-boundary"><Code2 size={25} /><strong>{pruningRuns.length}</strong><span>pruned inputs</span></div></section>
    {error && <div className="error-banner"><AlertTriangle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
    <section className="anchor-layout">
      <aside className="panel anchor-history"><div className="panel-head"><div><span className="step">H</span><h2>MQL runs</h2></div><button className="anchor-icon-button" onClick={() => void refresh()} title="Refresh"><RotateCw size={15} /></button></div><div className="anchor-boundary-note"><DatabaseZap size={15} /><span>Every draft, validation issue, repair, execution preview, and metric result survives refresh.</span></div><div className="anchor-history-list">{runs.map((run) => <button key={run.run_id} className={active?.run_id === run.run_id ? "active" : ""} onClick={() => selectLocal(run)}><span className={`run-status status-${run.status}`}><i />{run.status}</span><small>{relativeTime(run.updated_at)}</small><strong>{run.question}</strong><span>{run.database_id} / {run.candidates.length} candidates / {run.selected_combination_rank ? `selected #${run.selected_combination_rank}` : "pending"}</span></button>)}{!runs.length && <div className="empty-history">No MQL generation runs yet.</div>}</div></aside>
      <div className="anchor-content">
        <section className="panel nm-composer"><div className="panel-head"><div><span className="step">01</span><h2>Generate executable MQL</h2></div><span className="nm-readiness ready">Read-only execution</span></div>
          <div className="mql-input-grid"><label><span>Completed schema-pruning run</span><select value={pruningId} onChange={(event) => choosePruning(event.target.value)}>{pruningRuns.map((item) => <option key={item.run_id} value={item.run_id}>{item.database_id} / {item.run_id.slice(0, 8)} / {item.pruned_schemas.length} alternatives</option>)}</select></label><label><span>Repair attempts</span><input type="number" min="0" max="3" value={repairAttempts} onChange={(event) => setRepairAttempts(Number(event.target.value))} /></label><label><span>Preview limit</span><input type="number" min="1" max="500" value={previewLimit} onChange={(event) => setPreviewLimit(Number(event.target.value))} /></label><button disabled={busy || !pruningId || !ranks.length} onClick={() => void start()}>{busy ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />} Generate MQL</button></div>
          {selectedPruning && <div className="mql-rank-picker"><span>Schema alternatives</span>{selectedPruning.pruned_schemas.map((schema) => <label key={schema.combination_rank} className={ranks.includes(schema.combination_rank) ? "selected" : ""}><input type="checkbox" checked={ranks.includes(schema.combination_rank)} onChange={() => toggleRank(schema.combination_rank)} /><strong>#{schema.combination_rank}</strong><small>{percent(schema.score)} / {schema.nodes.length} paths</small></label>)}</div>}
          <div className="anchor-composer-foot"><span>Question + selected pruned schema only / deterministic path and reference enforcement</span><span>{selectedPruning?.pruned_schemas.length ?? 0} alternatives available</span></div>
        </section>
        {active ? <section className="panel anchor-monitor"><div className="anchor-run-head"><div><span className="eyebrow">MQL {active.run_id.slice(0, 8)} / {active.model_id}</span><h2>{active.question}</h2></div><div className="anchor-progress"><span className={`run-status status-${active.status}`}><i />{active.status}</span><strong>{progress}%</strong><div><i style={{ width: `${progress}%` }} /></div></div></div>{active.failure && <div className="error-banner embedded"><AlertTriangle size={15} />{active.failure}</div>}<div className="nm-metrics"><Metric label="Alternatives" value={active.config.combination_ranks.length} /><Metric label="Generated" value={active.candidates.length} /><Metric label="Valid/executed" value={active.candidates.filter((item) => ["valid", "executed", "selected"].includes(item.status)).length} /><Metric label="Repairs" value={active.candidates.reduce((sum, item) => sum + item.repairs.length, 0)} /><Metric label="Selected" value={active.selected_combination_rank ?? 0} /></div><div className="anchor-stage-grid"><MQLTimeline stages={active.stages} selected={selectedStage} onSelect={setSelectedStage} /><article className="anchor-artifact"><header><div><span className="eyebrow">INTERMEDIATE MQL RESULT</span><strong>{LABELS[selectedStage]}</strong></div>{trace?.duration_ms != null && <span><Clock3 size={12} />{formatDuration(trace.duration_ms)}</span>}</header><div className="anchor-artifact-scroll"><MQLArtifact run={active} stage={selectedStage} trace={trace} /></div></article></div></section> : <section className="panel anchor-empty"><Code2 size={34} /><span className="eyebrow">SCHEMA ALTERNATIVES TO PIPELINES</span><h2>Select a completed pruning run.</h2><p>All chosen alternatives are generated independently. Empty results remain valid; selection follows schema score among candidates that actually execute.</p></section>}
      </div>
    </section>
  </main>;
}

function MQLTimeline({ stages, selected, onSelect }: { stages: PruningStageTrace[]; selected: string; onSelect: (stage: string) => void }) {
  return <aside className="anchor-timeline"><div className="anchor-timeline-title"><Code2 size={13} /> MQL trace</div>{stages.map((stage, index) => <button key={stage.stage} className={`${stage.status} ${selected === stage.stage ? "selected" : ""}`} onClick={() => onSelect(stage.stage)}><span className="anchor-stage-icon">{stage.status === "completed" ? <Check size={13} /> : stage.status === "running" ? <LoaderCircle size={13} className="spin" /> : stage.status === "failed" ? <X size={13} /> : <CircleDashed size={13} />}</span><span><strong>{LABELS[stage.stage]}</strong><small>{stage.summary || "Waiting"}</small></span>{stage.duration_ms != null && <i>{formatDuration(stage.duration_ms)}</i>}{index < stages.length - 1 && <b />}</button>)}</aside>;
}

function MQLArtifact({ run, stage, trace }: { run: MQLGenerationRunView; stage: string; trace?: PruningStageTrace }) {
  if (!trace || !["completed", "failed"].includes(trace.status)) return <Waiting trace={trace} />;
  if (trace.status === "failed") return <div className="anchor-stack"><Hero icon={<AlertTriangle size={20} />} label="STAGE FAILED" title={trace.error ?? "Generation failed"} detail="Completed earlier artifacts remain available in the timeline." /><Raw value={trace.artifact} /></div>;
  if (stage === "input_preparation") return <InputView value={trace.artifact} />;
  if (stage === "candidate_generation") return <CandidateView candidates={run.candidates} mode="draft" />;
  if (stage === "deterministic_validation") return <CandidateView candidates={run.candidates} mode="validation" />;
  if (stage === "repair") return <RepairView candidates={run.candidates} />;
  if (stage === "execution_selection") return <ExecutionView run={run} />;
  if (stage === "tend_evaluation") return <FinalView run={run} />;
  return <Raw value={trace.artifact} />;
}

function InputView({ value }: { value: unknown }) {
  const alternatives = Array.isArray(value) ? value as Array<Record<string, unknown>> : [];
  return <div className="anchor-stack"><Hero icon={<GitBranch size={20} />} label="DIRECT GENERATION INPUT" title={`${alternatives.length} compact schema alternatives`} detail="No full schema, sample documents, or hidden retrieval candidates are added to the prompt." /><div className="mql-input-cards">{alternatives.map((item) => <article key={String(item.combination_rank)}><span>Alternative {String(item.combination_rank)}</span><strong>{percent(Number(item.schema_score))}</strong><small>{Array.isArray(item.nodes) ? item.nodes.length : 0} schema nodes / {Array.isArray(item.reference_edges) ? item.reference_edges.length : 0} references</small></article>)}</div><Raw value={value} /></div>;
}

function CandidateView({ candidates, mode }: { candidates: MQLCandidate[]; mode: "draft" | "validation" }) {
  return <div className="anchor-stack"><Hero icon={mode === "draft" ? <Sparkles size={20} /> : <Gauge size={20} />} label={mode === "draft" ? "MODEL OUTPUTS" : "DETERMINISTIC CHECKS"} title={`${candidates.length} MQL candidates`} detail={mode === "draft" ? "One independent candidate is generated for each selected schema alternative." : "JSON, read-only policy, pruned paths, reference edges, and a one-row MongoDB probe are checked."} /><div className="mql-candidate-grid">{candidates.map((candidate) => <article key={candidate.combination_rank}><header><span>Alternative {candidate.combination_rank}</span><strong className={`status-${candidate.status}`}>{candidate.status.replaceAll("_", " ")}</strong></header><div className="mql-candidate-meta"><span>{candidate.collection ?? "no collection"}</span><span>{candidate.pipeline.length} stages</span><span>{percent(candidate.schema_score)}</span></div>{candidate.rationale && <p>{candidate.rationale}</p>}<pre>{candidate.mql ?? candidate.failure ?? "No candidate returned"}</pre>{mode === "validation" && <IssueList values={candidate.validation_issues} />}</article>)}</div><Raw value={candidates} /></div>;
}

function RepairView({ candidates }: { candidates: MQLCandidate[] }) {
  const attempts = candidates.flatMap((candidate) => candidate.repairs.map((repair) => ({ candidate, repair })));
  return <div className="anchor-stack"><Hero icon={<Wrench size={20} />} label="LIMITED ERROR-GROUNDED REPAIR" title={`${attempts.length} repair attempts`} detail="The model receives its rejected pipeline and deterministic errors but cannot introduce new schema paths." />{attempts.length ? <div className="mql-repair-list">{attempts.map(({ candidate, repair }) => <article key={`${candidate.combination_rank}-${repair.attempt}`}><header><span>Alternative {candidate.combination_rank} / attempt {repair.attempt}</span><strong className={repair.accepted ? "accepted" : "rejected"}>{repair.accepted ? "accepted" : "rejected"}</strong></header><IssueList values={repair.input_issues} /><pre>{repair.collection ? `db.${repair.collection}.aggregate(${JSON.stringify(repair.pipeline)})` : "No repaired candidate"}</pre><IssueList values={repair.validation_issues} /></article>)}</div> : <div className="anchor-waiting"><Check size={25} /><h3>No repair was needed</h3><p>Every generated candidate passed deterministic validation and the MongoDB probe.</p></div>}<Raw value={attempts} /></div>;
}

function ExecutionView({ run }: { run: MQLGenerationRunView }) {
  return <div className="anchor-stack"><Hero icon={<DatabaseZap size={20} />} label="EXECUTE ALL, THEN SELECT" title={run.selected_combination_rank ? `Alternative ${run.selected_combination_rank} selected` : "Execution results"} detail="Successful candidates retain their previews; selection uses the original schema score, including when a correct query returns zero rows." /><div className="mql-execution-grid">{run.candidates.map((candidate) => <article key={candidate.combination_rank} className={candidate.status === "selected" ? "selected" : ""}><header><strong>#{candidate.combination_rank}</strong><span>{candidate.status.replaceAll("_", " ")}</span></header><b>{candidate.execution?.ok ? `${candidate.execution.row_count} preview rows` : candidate.failure ?? "Not executed"}</b><small>{candidate.execution?.latency_ms == null ? "-" : formatDuration(candidate.execution.latency_ms)} / schema {percent(candidate.schema_score)}</small></article>)}</div>{run.final_mql && <CodeBlock title="Selected executable MQL" value={run.final_mql} />}{run.final_result && <ResultPreview value={run.final_result.rows} truncated={run.final_result.possibly_truncated} />}<Raw value={run.candidates} /></div>;
}

function FinalView({ run }: { run: MQLGenerationRunView }) {
  const metrics = run.tend_metrics;
  return <div className="anchor-stack mql-final-stack"><section className="anchor-bundle-hero"><Check size={23} /><div><span className="eyebrow">FINAL EXECUTABLE RESULT</span><h2>{run.final_collection ? `db.${run.final_collection}.aggregate(...)` : "No selected pipeline"}</h2><p>Schema alternative {run.selected_combination_rank ?? "-"} / {run.final_pipeline.length} stages / {run.final_result?.row_count ?? 0} preview rows</p></div><strong>{run.final_result?.ok ? "OK" : "-"}<small>execution</small></strong></section>{run.final_mql && <CodeBlock title="Final MQL" value={run.final_mql} />}{run.final_result && <ResultPreview value={run.final_result.rows} truncated={run.final_result.possibly_truncated} />}<section className="mql-metric-panel"><header><div><Gauge size={16} /><strong>Four TEND result dimensions</strong></div><span>{metrics.available ? `record ${metrics.record_id} / ${metrics.track}` : "custom question"}</span></header>{!metrics.available && <div className="mql-metric-notice"><AlertTriangle size={14} /><span><strong>Official correctness metrics unavailable</strong>{metrics.reason}</span></div>}<div className="mql-four-metrics"><MetricCard label="EXC" value={metrics.EXC == null ? "N/A" : percent(metrics.EXC)} detail="Bounded column-tolerant execution accuracy" available={metrics.available} /><MetricCard label="EXF1" value={metrics.EXF1 == null ? "N/A" : percent(metrics.EXF1)} detail="Name-insensitive row-multiset F1" available={metrics.available} /><MetricCard label="Outcome" value={metrics.outcome?.replaceAll("_", " ") ?? "N/A"} detail={metrics.available ? `${metrics.predicted_row_count} predicted / ${metrics.gold_row_count} gold rows` : "Requires an official gold result"} available={metrics.available} /><MetricCard label="Claim axes" value={metrics.available ? `${Object.keys(metrics.claim_axes).length} axes` : "N/A"} detail="Domain and structural reporting slices" available={metrics.available} /></div>{metrics.available && <div className="mql-claim-axes">{Object.entries(metrics.claim_axes).map(([key, value]) => <span key={key}><small>{key.replaceAll("_", " ")}</small><strong>{value}</strong></span>)}</div>}</section><Raw value={{ metrics, final_result: run.final_result }} /></div>;
}

function IssueList({ values }: { values: MQLValidationIssue[] }) { return <div className="mql-issue-list">{values.map((issue, index) => <div key={`${issue.code}-${index}`} className={issue.severity}><strong>{issue.code}</strong><span>{issue.message}</span>{issue.stage_index != null && <small>stage {issue.stage_index + 1}</small>}</div>)}{!values.length && <div className="info"><strong>NO ISSUES</strong><span>No validation feedback.</span></div>}</div>; }
function ResultPreview({ value, truncated }: { value: Array<Record<string, unknown>>; truncated: boolean }) { return <section className="mql-result-preview"><header><strong>Execution preview</strong><span>{value.length} rows{truncated ? " / truncated" : ""}</span></header><pre>{JSON.stringify(value, null, 2)}</pre></section>; }
function CodeBlock({ title, value }: { title: string; value: string }) { return <section className="mql-code"><header><Code2 size={14} /><strong>{title}</strong></header><pre>{value}</pre></section>; }
function MetricCard({ label, value, detail, available }: { label: string; value: string; detail: string; available: boolean }) { return <article className={available ? "available" : "unavailable"}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>; }
function Metric({ label, value }: { label: string; value: number }) { return <div><span>{label}</span><strong>{value.toLocaleString()}</strong></div>; }
function Hero({ icon, label, title, detail }: { icon: React.ReactNode; label: string; title: string; detail: string }) { return <section className="anchor-artifact-hero"><span>{icon}</span><div><small className="eyebrow">{label}</small><h2>{title}</h2><p>{detail}</p></div></section>; }
function Waiting({ trace }: { trace?: PruningStageTrace }) { return <div className="anchor-waiting"><Clock3 size={25} /><h3>{trace ? LABELS[trace.stage] : "Stage"}</h3><p>{trace?.status === "running" ? trace.summary || "Processing now." : "This stage has not completed yet."}</p></div>; }
function Raw({ value }: { value: unknown }) { return <details className="anchor-raw"><summary><FileJson2 size={13} /> Raw JSON</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>; }
function currentStage(run: MQLGenerationRunView) { return run.stages.find((item) => item.status === "running")?.stage ?? [...run.stages].reverse().find((item) => item.status === "completed")?.stage ?? run.stages.find((item) => item.status === "failed")?.stage ?? "input_preparation"; }
function percent(value: number) { return `${Math.round(value * 1000) / 10}%`; }
function formatDuration(value: number) { return value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`; }
function relativeTime(value: string) { const minutes = Math.floor((Date.now() - new Date(value).getTime()) / 60_000); if (minutes < 1) return "now"; if (minutes < 60) return `${minutes}m`; const hours = Math.floor(minutes / 60); return hours < 24 ? `${hours}h` : `${Math.floor(hours / 24)}d`; }
function message(value: unknown) { return value instanceof Error ? value.message : String(value); }
