import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleDashed,
  Clock3,
  FileJson2,
  GitBranch,
  LoaderCircle,
  Maximize2,
  Network,
  RotateCw,
  Search,
  SlidersHorizontal,
  Sparkles,
  ZoomIn,
  ZoomOut,
  X
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type {
  AnchorRunView,
  CandidateCombination,
  PathCandidate,
  PrunedSchema,
  PruningStageTrace,
  RelationshipEvaluation,
  SchemaIndexRunView,
  SchemaPruningRunView,
  SearchRequest
} from "./types";

const ACTIVE_KEY = "tend-new-methods-pruning-run";
const LABELS: Record<string, string> = {
  request_compilation: "Search requests",
  local_scoring: "Local path scoring",
  relationship_scoring: "Relationship scoring",
  combination_search: "Combination search",
  schema_pruning: "Pruned schema"
};
const DEFAULT_CONFIG: SchemaPruningRunView["config"] = {
  primary_top_k: 10,
  supporting_top_k: 5,
  beam_width: 20,
  final_top_k: 3,
  alternative_margin: 0.05,
  local_weight: 0.75,
  relationship_weight: 0.25
};
const terminal = new Set(["completed", "failed"]);

export function SchemaPruningPanel() {
  const [bundles, setBundles] = useState<AnchorRunView[]>([]);
  const [indexes, setIndexes] = useState<SchemaIndexRunView[]>([]);
  const [runs, setRuns] = useState<SchemaPruningRunView[]>([]);
  const [bundleId, setBundleId] = useState("");
  const [indexId, setIndexId] = useState("");
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [active, setActive] = useState<SchemaPruningRunView | null>(null);
  const [selectedStage, setSelectedStage] = useState("request_compilation");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const [bundleValues, indexValues, runValues] = await Promise.all([
      api.anchorRuns(200),
      api.schemaIndexRuns(200),
      api.schemaPruningRuns(100)
    ]);
    const completedBundles = bundleValues.filter((item) => item.status === "completed" && item.retrieval_bundle);
    const completedIndexes = indexValues.filter((item) => item.status === "completed");
    setBundles(completedBundles);
    setIndexes(completedIndexes);
    setBundleId((current) => current || completedBundles[0]?.run_id || "");
    setIndexId((current) => current || completedIndexes[0]?.index_id || "");
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
        const updated = await api.schemaPruningRun(active.run_id);
        selectLocal(updated);
        setRuns((current) => [updated, ...current.filter((item) => item.run_id !== updated.run_id)]);
      } catch (value) {
        setError(message(value));
      }
    }, 700);
    return () => window.clearInterval(timer);
  }, [active?.run_id, active?.status]);

  const start = async () => {
    if (!bundleId || !indexId) return;
    setBusy(true);
    setError("");
    try {
      const run = await api.createSchemaPruningRun({
        bundle_run_id: bundleId,
        index_id: indexId,
        config
      });
      selectLocal(run);
      setRuns((current) => [run, ...current]);
    } catch (value) {
      setError(message(value));
    } finally {
      setBusy(false);
    }
  };

  const selectLocal = (run: SchemaPruningRunView) => {
    setActive(run);
    window.localStorage.setItem(ACTIVE_KEY, run.run_id);
    setSelectedStage(currentStage(run));
  };
  const progress = useMemo(() => {
    if (!active?.stages.length) return 0;
    return Math.round(active.stages.reduce((sum, item) => sum + item.progress, 0) / active.stages.length * 100);
  }, [active]);
  const selectedBundle = bundles.find((item) => item.run_id === bundleId);
  const trace = active?.stages.find((item) => item.stage === selectedStage);

  return <main className="anchor-main nm-main">
    <section className="anchor-intro"><div><span className="eyebrow">NEW METHOD / ONLINE SCHEMA PRUNING</span><h1>Ground targets jointly.<br /><em>Keep the evidence visible.</em></h1><p>Score every eligible path, validate relationships, compare joint combinations, and retain connected schema alternatives.</p></div><div className="anchor-boundary"><Network size={25} /><strong>{indexes.length}</strong><span>ready indexes</span></div></section>
    {error && <div className="error-banner"><AlertTriangle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
    <section className="anchor-layout">
      <aside className="panel anchor-history"><div className="panel-head"><div><span className="step">H</span><h2>Pruning runs</h2></div><button className="anchor-icon-button" onClick={() => void refresh()} title="Refresh"><RotateCw size={15} /></button></div><div className="anchor-boundary-note"><GitBranch size={15} /><span>Each run keeps requests, signals, relationship routes, combinations, and final trees.</span></div><div className="anchor-history-list">{runs.map((run) => <button key={run.run_id} className={active?.run_id === run.run_id ? "active" : ""} onClick={() => selectLocal(run)}><span className={`run-status status-${run.status}`}><i />{run.status}</span><small>{relativeTime(run.updated_at)}</small><strong>{run.database_id}</strong><span>{Object.keys(run.candidates_by_target).length} targets / {run.pruned_schemas.length} schemas</span></button>)}{!runs.length && <div className="empty-history">No pruning runs yet.</div>}</div></aside>
      <div className="anchor-content">
        <section className="panel nm-composer"><div className="panel-head"><div><span className="step">01</span><h2>Retrieve candidate schema paths</h2></div><span className="nm-readiness ready">Persisted trace</span></div>
          <div className="nm-selector-grid"><label><span>Completed RetrievalBundle</span><select value={bundleId} onChange={(event) => setBundleId(event.target.value)}>{bundles.map((item) => <option key={item.run_id} value={item.run_id}>{item.question.slice(0, 95)}</option>)}</select></label><label><span>Completed schema index</span><select value={indexId} onChange={(event) => setIndexId(event.target.value)}>{indexes.map((item) => <option key={item.index_id} value={item.index_id}>{item.database_id} / {item.index_id.slice(0, 8)} / {item.node_count.toLocaleString()} nodes</option>)}</select></label><button disabled={busy || !bundleId || !indexId} onClick={() => void start()}>{busy ? <LoaderCircle size={16} className="spin" /> : <Search size={16} />} Run pruning</button></div>
          {selectedBundle && <div className="nm-selected-question"><Sparkles size={15} /><span><strong>{selectedBundle.retrieval_bundle?.targets.length ?? 0} targets</strong>{selectedBundle.question}</span></div>}
          <details className="nm-advanced"><summary><SlidersHorizontal size={14} /> Retrieval configuration <ChevronDown size={13} /></summary><div className="nm-config-grid"><NumberInput label="Primary top-k" value={config.primary_top_k} min={1} max={50} onChange={(value) => setConfig({ ...config, primary_top_k: value })} /><NumberInput label="Supporting top-k" value={config.supporting_top_k} min={1} max={50} onChange={(value) => setConfig({ ...config, supporting_top_k: value })} /><NumberInput label="Beam width" value={config.beam_width} min={1} max={500} onChange={(value) => setConfig({ ...config, beam_width: value })} /><NumberInput label="Final alternatives" value={config.final_top_k} min={1} max={10} onChange={(value) => setConfig({ ...config, final_top_k: value })} /><NumberInput label="Alternative margin" value={config.alternative_margin} min={0} max={1} step={0.01} onChange={(value) => setConfig({ ...config, alternative_margin: value })} /></div></details>
          <div className="anchor-composer-foot"><span>Local 0.75 / relationship 0.25 / exact membership preserved</span><span>{bundles.length} bundles available</span></div>
        </section>
        {active ? <section className="panel anchor-monitor"><div className="anchor-run-head"><div><span className="eyebrow">RETRIEVAL {active.run_id.slice(0, 8)} / {active.database_id}</span><h2>{bundles.find((item) => item.run_id === active.bundle_run_id)?.question ?? `Bundle ${active.bundle_run_id.slice(0, 8)}`}</h2></div><div className="anchor-progress"><span className={`run-status status-${active.status}`}><i />{active.status}</span><strong>{progress}%</strong><div><i style={{ width: `${progress}%` }} /></div></div></div>{active.failure && <div className="error-banner embedded"><AlertTriangle size={15} />{active.failure}</div>}<div className="nm-metrics"><Metric label="Requests" value={active.search_requests.length} /><Metric label="Candidate paths" value={Object.values(active.candidates_by_target).flat().length} /><Metric label="Pair scores" value={active.relationship_evaluations.length} /><Metric label="Alternatives" value={active.combinations.length} /><Metric label="Pruned trees" value={active.pruned_schemas.length} /></div><div className="anchor-stage-grid"><PruningTimeline stages={active.stages} selected={selectedStage} onSelect={setSelectedStage} /><article className="anchor-artifact"><header><div><span className="eyebrow">INTERMEDIATE RETRIEVAL RESULT</span><strong>{LABELS[selectedStage] ?? selectedStage}</strong></div>{trace?.duration_ms != null && <span><Clock3 size={12} />{formatDuration(trace.duration_ms)}</span>}</header><div className="anchor-artifact-scroll"><PruningArtifact run={active} stage={selectedStage} trace={trace} /></div></article></div></section> : <section className="panel anchor-empty"><Search size={34} /><span className="eyebrow">BUNDLE + INDEX</span><h2>Select persisted inputs and start schema pruning.</h2><p>The result is reproducible: every search request, local signal, relationship route, beam decision, and selected path is saved in SQLite.</p></section>}
      </div>
    </section>
  </main>;
}

function PruningTimeline({ stages, selected, onSelect }: { stages: PruningStageTrace[]; selected: string; onSelect: (stage: string) => void }) {
  return <aside className="anchor-timeline"><div className="anchor-timeline-title"><Search size={13} /> Retrieval trace</div>{stages.map((stage, index) => <button key={stage.stage} className={`${stage.status} ${selected === stage.stage ? "selected" : ""}`} onClick={() => onSelect(stage.stage)}><span className="anchor-stage-icon">{stage.status === "completed" ? <Check size={13} /> : stage.status === "running" ? <LoaderCircle size={13} className="spin" /> : stage.status === "failed" ? <X size={13} /> : <CircleDashed size={13} />}</span><span><strong>{LABELS[stage.stage]}</strong><small>{stage.summary || "Waiting"}</small></span>{stage.duration_ms != null && <i>{formatDuration(stage.duration_ms)}</i>}{index < stages.length - 1 && <b />}</button>)}</aside>;
}

function PruningArtifact({ run, stage, trace }: { run: SchemaPruningRunView; stage: string; trace?: PruningStageTrace }) {
  if (!trace || !["completed", "failed"].includes(trace.status)) return <Waiting trace={trace} />;
  if (trace.status === "failed") return <Raw value={{ error: trace.error, artifact: trace.artifact }} />;
  if (stage === "request_compilation") return <RequestsView values={run.search_requests} />;
  if (stage === "local_scoring") return <CandidatesView values={run.candidates_by_target} />;
  if (stage === "relationship_scoring") return <RelationshipsView values={run.relationship_evaluations} />;
  if (stage === "combination_search") return <CombinationsView values={run.combinations} />;
  if (stage === "schema_pruning") return <PrunedSchemasView values={run.pruned_schemas} />;
  return <Raw value={trace.artifact} />;
}

function RequestsView({ values }: { values: SearchRequest[] }) {
  return <div className="anchor-stack"><Hero icon={<Search size={20} />} label="MINIMAL SEARCH REQUESTS" title={`${values.length} targets compiled`} detail="query_text is canonical + aliases; context, type, and values remain separate scoring channels." /><div className="anchor-card-grid">{values.map((item) => <article className="anchor-card" key={item.target_id}><div><code>{item.target_id}</code><span className={`anchor-kind ${item.kind}`}>{item.kind.replaceAll("_", " ")}</span><strong>{item.role}</strong></div><h3>{item.query_text}</h3><p>Context: {item.context_text || "inactive"}</p><footer>{item.expected_types.map((type) => <span key={type}>{type}</span>)}<span>{item.value_constraints.length} value constraints</span></footer></article>)}</div><Raw value={values} /></div>;
}

function CandidatesView({ values }: { values: Record<string, PathCandidate[]> }) {
  return <div className="anchor-stack"><Hero icon={<Sparkles size={20} />} label="NORMALIZED ACTIVE-WEIGHT SCORING" title={`${Object.values(values).flat().length} candidates retained`} detail="Missing context, type, or value evidence removes that channel and renormalizes the remaining weights." />{Object.entries(values).map(([targetId, candidates]) => <section className="anchor-section nm-candidate-section" key={targetId}><header><strong>{targetId}</strong><span>{candidates.length}</span></header><div className="nm-candidate-table">{candidates.map((candidate, index) => <article key={candidate.path_id}><b>{index + 1}</b><div><strong>{candidate.collection}.{candidate.path || "root"}</strong><code>{candidate.path_id}</code></div><Signal label="final" value={candidate.score} /><Signal label="name" value={candidate.signals.name} /><Signal label="context" value={candidate.signals.context} /><Signal label="type" value={candidate.signals.type} /><Signal label="value" value={candidate.signals.value} /><span className={`nm-evidence ${candidate.signals.exact_contains === true ? "yes" : candidate.signals.exact_contains === false ? "no" : "na"}`}>{candidate.signals.exact_contains == null ? "no exact test" : candidate.signals.exact_contains ? "exact observed" : "not observed"}</span></article>)}</div></section>)}<Raw value={values} /></div>;
}

function RelationshipsView({ values }: { values: RelationshipEvaluation[] }) {
  const ordered = [...values].sort((left, right) => right.score - left.score);
  return <div className="anchor-stack"><Hero icon={<GitBranch size={20} />} label="PAIRWISE STRUCTURAL EVIDENCE" title={`${values.length} candidate pairs evaluated`} detail="Parent-child edges cost 1; confirmed same/cross-collection references cost 2 and are returned as connector evidence." /><div className="nm-relationship-list">{ordered.slice(0, 250).map((item, index) => <article key={`${item.relation_index}-${item.source_path_id}-${item.target_path_id}-${index}`}><div><span>{item.relation_type.replaceAll("_", " ")}</span><strong>{percent(item.score)}</strong></div><code>{item.source_path_id}</code><i>to</i><code>{item.target_path_id}</code><footer><span>{item.resolved ? `${item.distance ?? 0} distance` : "unresolved"}</span><span>{item.connector_node_ids.length} connector nodes</span><span>{item.reference_edges.length} reference edges</span></footer></article>)}</div>{ordered.length > 250 && <p className="nm-truncated">Showing the 250 highest pair scores. Raw JSON contains every evaluation.</p>}<Raw value={values} /></div>;
}

function CombinationsView({ values }: { values: CandidateCombination[] }) {
  return <div className="anchor-stack"><Hero icon={<GitBranch size={20} />} label="BEAM SEARCH" title={`${values.length} alternatives retained`} detail="Best combination plus near-best alternatives within the configured margin, capped by final top-k." /><div className="nm-combination-grid">{values.map((item) => <article key={item.rank}><header><span>Alternative {item.rank}</span><strong>{percent(item.final_score)}</strong></header><div className="nm-score-split"><span>Local <b>{percent(item.local_score)}</b></span><span>Relationships <b>{percent(item.relationship_score)}</b></span></div><dl>{Object.entries(item.selections).map(([target, path]) => <div key={target}><dt>{target}</dt><dd>{path}</dd></div>)}</dl><footer>{item.connector_node_ids.length} connectors / {item.reference_edges.length} references</footer></article>)}</div><Raw value={values} /></div>;
}

function PrunedSchemasView({ values }: { values: PrunedSchema[] }) {
  const [rank, setRank] = useState(values[0]?.combination_rank ?? 1);
  const selected = values.find((item) => item.combination_rank === rank) ?? values[0];
  if (!selected) return <Waiting />;
  return <div className="anchor-stack nm-pruned-stack"><section className="anchor-bundle-hero"><Check size={23} /><div><span className="eyebrow">CONNECTED PRUNED SCHEMA</span><h2>Alternative {selected.combination_rank}</h2><p>{selected.nodes.length} paths / {selected.reference_edges.length} reference edges / score {percent(selected.score)}</p></div><label className="nm-alternative-select"><span>Alternative</span><select value={rank} onChange={(event) => setRank(Number(event.target.value))}>{values.map((item) => <option key={item.combination_rank} value={item.combination_rank}>#{item.combination_rank} / {percent(item.score)}</option>)}</select></label></section><PrunedSchemaTree value={selected} /><Raw value={selected} /></div>;
}

function PrunedSchemaTree({ value }: { value: PrunedSchema }) {
  const [zoom, setZoom] = useState(0.9);
  const [selectedId, setSelectedId] = useState(value.nodes.find((item) => item.role === "target")?.id ?? value.nodes[0]?.id ?? "");
  const nodeById = useMemo(() => new Map(value.nodes.map((item) => [item.id, item])), [value]);
  const layout = useMemo(() => layoutNodes(value), [value]);
  const selected = nodeById.get(selectedId);
  return <section className="nm-schema-workspace"><div className="nm-schema-canvas"><div className="nm-tree-toolbar"><button onClick={() => setZoom((item) => Math.min(1.7, item + 0.15))} title="Zoom in"><ZoomIn size={14} /></button><button onClick={() => setZoom((item) => Math.max(0.45, item - 0.15))} title="Zoom out"><ZoomOut size={14} /></button><button onClick={() => setZoom(0.9)} title="Fit"><Maximize2 size={14} /></button><span>{Math.round(zoom * 100)}%</span></div><svg width={layout.width * zoom} height={layout.height * zoom} viewBox={`0 0 ${layout.width} ${layout.height}`} aria-label="Pruned schema tree">{layout.edges.map((edge) => <path key={edge.id} className={edge.reference ? "reference" : ""} d={`M ${edge.x1} ${edge.y1} C ${edge.x1 + 65} ${edge.y1}, ${edge.x2 - 65} ${edge.y2}, ${edge.x2} ${edge.y2}`} />)}{layout.nodes.map((position) => { const node = nodeById.get(position.id)!; return <g key={node.id} className={`nm-schema-node ${node.role} ${selectedId === node.id ? "selected" : ""}`} transform={`translate(${position.x} ${position.y})`} onClick={() => setSelectedId(node.id)} role="button" tabIndex={0}><rect width="190" height="61" rx="8" /><rect className="accent" width="5" height="61" rx="3" /><text className="kind" x="15" y="17">{node.role.toUpperCase()}</text><text className="title" x="15" y="36">{truncate(node.path || node.collection, 27)}</text><text className="meta" x="15" y="51">{node.collection} / {node.types.join(", ") || "unknown"}</text></g>; })}</svg></div><aside className="nm-schema-inspector">{selected ? <><span className={`nm-node-role ${selected.role}`}>{selected.role}</span><h3>{selected.path || selected.collection}</h3><code>{selected.id}</code><dl><div><dt>Collection</dt><dd>{selected.collection}</dd></div><div><dt>Types</dt><dd>{selected.types.join(", ") || "unknown"}</dd></div><div><dt>Parent</dt><dd>{selected.parent_id ?? "root"}</dd></div><div><dt>Targets</dt><dd>{selected.target_ids.join(", ") || "none"}</dd></div></dl>{selected.binding && <Raw value={selected.binding} />}</> : <p>Select a node to inspect its role and binding.</p>}</aside></section>;
}

function layoutNodes(value: PrunedSchema) {
  const ids = new Set(value.nodes.map((item) => item.id));
  const depths = new Map<string, number>();
  const depthOf = (id: string): number => {
    if (depths.has(id)) return depths.get(id)!;
    const node = value.nodes.find((item) => item.id === id);
    const depth = node?.parent_id && ids.has(node.parent_id) ? depthOf(node.parent_id) + 1 : 0;
    depths.set(id, depth);
    return depth;
  };
  value.nodes.forEach((item) => depthOf(item.id));
  const levels = new Map<number, string[]>();
  value.nodes.forEach((item) => levels.set(depthOf(item.id), [...(levels.get(depthOf(item.id)) ?? []), item.id]));
  const positions = new Map<string, { x: number; y: number }>();
  [...levels.entries()].forEach(([depth, level]) => level.sort().forEach((id, row) => positions.set(id, { x: 35 + depth * 245, y: 32 + row * 82 })));
  const nodes = value.nodes.map((item) => ({ id: item.id, ...positions.get(item.id)! }));
  const edges = value.nodes.filter((item) => item.parent_id && positions.has(item.parent_id)).map((item) => { const source = positions.get(item.parent_id!)!; const target = positions.get(item.id)!; return { id: `${item.parent_id}-${item.id}`, x1: source.x + 190, y1: source.y + 30, x2: target.x, y2: target.y + 30, reference: false }; });
  value.reference_edges.forEach((edge) => { const source = positions.get(edge.source_id); const target = positions.get(edge.target_id); if (source && target) edges.push({ id: `ref-${edge.source_id}-${edge.target_id}`, x1: source.x + 190, y1: source.y + 30, x2: target.x, y2: target.y + 30, reference: true }); });
  return { nodes, edges, width: Math.max(500, (Math.max(...depths.values(), 0) + 1) * 245 + 30), height: Math.max(360, Math.max(...[...levels.values()].map((items) => items.length), 1) * 82 + 50) };
}

function Signal({ label, value }: { label: string; value: number | null }) { return <span className="nm-signal"><small>{label}</small><strong>{value == null ? "-" : percent(value)}</strong></span>; }
function Metric({ label, value }: { label: string; value: number }) { return <div><span>{label}</span><strong>{value.toLocaleString()}</strong></div>; }
function NumberInput({ label, value, min, max, step = 1, onChange }: { label: string; value: number; min: number; max: number; step?: number; onChange: (value: number) => void }) { return <label><span>{label}</span><input type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} /></label>; }
function Hero({ icon, label, title, detail }: { icon: React.ReactNode; label: string; title: string; detail: string }) { return <section className="anchor-artifact-hero"><span>{icon}</span><div><small className="eyebrow">{label}</small><h2>{title}</h2><p>{detail}</p></div></section>; }
function Waiting({ trace }: { trace?: PruningStageTrace }) { return <div className="anchor-waiting"><Clock3 size={25} /><h3>{trace ? LABELS[trace.stage] : "No schema"}</h3><p>{trace?.status === "running" ? trace.summary || "Processing this stage." : "This stage has not completed yet."}</p></div>; }
function Raw({ value }: { value: unknown }) { return <details className="anchor-raw"><summary><FileJson2 size={13} /> Raw JSON</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>; }
function percent(value: number) { return `${Math.round(value * 1000) / 10}%`; }
function formatDuration(value: number) { return value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`; }
function truncate(value: string, limit: number) { return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`; }
function currentStage(run: SchemaPruningRunView) { return run.stages.find((item) => item.status === "running")?.stage ?? [...run.stages].reverse().find((item) => item.status === "completed")?.stage ?? run.stages.find((item) => item.status === "failed")?.stage ?? "request_compilation"; }
function relativeTime(value: string) { const minutes = Math.floor((Date.now() - new Date(value).getTime()) / 60_000); if (minutes < 1) return "now"; if (minutes < 60) return `${minutes}m`; const hours = Math.floor(minutes / 60); return hours < 24 ? `${hours}h` : `${Math.floor(hours / 24)}d`; }
function message(value: unknown) { return value instanceof Error ? value.message : String(value); }
