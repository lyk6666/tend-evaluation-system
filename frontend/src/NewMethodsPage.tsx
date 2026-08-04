import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Beaker,
  Braces,
  Check,
  CheckCircle2,
  CircleDashed,
  Clock3,
  FileJson2,
  LoaderCircle,
  Maximize2,
  Minus,
  Network,
  Plus,
  RotateCw,
  Sparkles,
  Tags,
  Trash2,
  ZoomIn,
  X
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import type {
  AnchorRelation,
  AnchorRunView,
  AnchorStageTrace,
  DeferredPlanCue,
  Health,
  NormalizedQuestion,
  RetrievalRestriction,
  TypedSemanticAnchor
} from "./types";

const EXAMPLE =
  "For the 2021 season, find the highest-scoring driver for each constructor that won at least one race. Return the constructor name, driver's full name, total points earned for that constructor, and number of podium finishes. Return all tied drivers and order the results by total points descending.";

const LABELS: Record<string, string> = {
  normalization: "Normalized question",
  target_extraction: "Retrieval targets",
  support_inference: "Supporting fields",
  restriction_binding: "Restriction binding",
  retrieval_graph: "Retrieval graph",
  retrieval_bundle: "RetrievalBundle"
};

const terminal = new Set(["completed", "failed"]);
const ACTIVE_RUN_KEY = "tend-new-methods-anchor-run";

export function NewMethodsPage({
  health,
  onNavigate
}: {
  health: Health | null;
  onNavigate: (page: "evaluation" | "new-methods") => void;
}) {
  const [runs, setRuns] = useState<AnchorRunView[]>([]);
  const [active, setActive] = useState<AnchorRunView | null>(null);
  const [question, setQuestion] = useState(EXAMPLE);
  const [mode, setMode] = useState<"auto" | "llm" | "deterministic">("auto");
  const [selectedStage, setSelectedStage] = useState("normalization");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const values = await api.anchorRuns();
    setRuns(values);
    setActive((current) => {
      const wantedId = current?.run_id ?? window.localStorage.getItem(ACTIVE_RUN_KEY);
      const selected = values.find((item) => item.run_id === wantedId) ?? values[0] ?? null;
      if (selected) {
        window.localStorage.setItem(ACTIVE_RUN_KEY, selected.run_id);
        setQuestion(selected.question);
        setMode(selected.mode);
        const stage = selected.stages.find((item) => item.status === "running")
          ?? [...selected.stages].reverse().find((item) => item.status === "completed");
        setSelectedStage(stage?.stage ?? "normalization");
      }
      return selected;
    });
  }, []);

  useEffect(() => {
    refresh().catch((value) => setError(String(value)));
  }, [refresh]);

  useEffect(() => {
    if (!active || terminal.has(active.status)) return;
    const interval = window.setInterval(async () => {
      try {
        const updated = await api.anchorRun(active.run_id);
        setActive(updated);
        setRuns((current) => [
          updated,
          ...current.filter((item) => item.run_id !== updated.run_id)
        ]);
        const running = updated.stages.find((item) => item.status === "running");
        if (running) setSelectedStage(running.stage);
        if (updated.status === "completed") setSelectedStage("retrieval_bundle");
      } catch (value) {
        setError(String(value));
      }
    }, 600);
    return () => window.clearInterval(interval);
  }, [active?.run_id, active?.status]);

  const start = async () => {
    if (question.trim().length < 3) return;
    setBusy(true);
    setError("");
    try {
      const run = await api.createAnchorRun({
        question,
        mode,
        locale: "en",
        timezone: "Asia/Shanghai"
      });
      setActive(run);
      window.localStorage.setItem(ACTIVE_RUN_KEY, run.run_id);
      setSelectedStage("normalization");
      setRuns((current) => [run, ...current.filter((item) => item.run_id !== run.run_id)]);
    } catch (value) {
      setError(value instanceof Error ? value.message : String(value));
    } finally {
      setBusy(false);
    }
  };

  const select = async (runId: string) => {
    try {
      const run = await api.anchorRun(runId);
      setActive(run);
      window.localStorage.setItem(ACTIVE_RUN_KEY, run.run_id);
      setQuestion(run.question);
      setMode(run.mode);
      const current = run.stages.find((item) => item.status === "running")
        ?? [...run.stages].reverse().find((item) => item.status === "completed");
      setSelectedStage(current?.stage ?? "normalization");
    } catch (value) {
      setError(String(value));
    }
  };

  const remove = async (runId: string) => {
    await api.deleteAnchorRun(runId);
    setRuns((current) => current.filter((item) => item.run_id !== runId));
    if (active?.run_id === runId) {
      window.localStorage.removeItem(ACTIVE_RUN_KEY);
      setActive(null);
    }
  };

  const progress = useMemo(() => {
    if (!active?.stages.length) return 0;
    return Math.round(
      (active.stages.filter((item) => item.status === "completed").length / active.stages.length) * 100
    );
  }, [active]);

  const trace = active?.stages.find((item) => item.stage === selectedStage);

  return (
    <div className="app-shell anchor-app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><Beaker size={21} /></div>
          <div><strong>TEND Lab</strong><span>Evaluation control plane</span></div>
        </div>
        <nav aria-label="Primary navigation">
          <button onClick={() => onNavigate("evaluation")}><ArrowLeft size={16} /> Evaluation</button>
          <button className="active"><Sparkles size={16} /> New Methods</button>
        </nav>
        <button className="refresh-button" onClick={() => void refresh()} title="Refresh anchor runs">
          <RotateCw size={15} />
        </button>
        <div className={`readiness ${health?.provider.ready ? "ready" : "waiting"}`}>
          <Activity size={16} /> {health?.provider.ready ? health.provider.model : "Fallback ready"}
        </div>
      </header>

      <main className="anchor-main">
        <section className="anchor-intro">
          <div>
            <span className="eyebrow">NEW METHOD · RETRIEVAL PREPARATION</span>
            <h1>Retrieval concepts.<br /><em>Only schema evidence.</em></h1>
            <p>Extract entities and fields, infer necessary supporting evidence, then bind restrictions without constructing a query plan.</p>
          </div>
          <div className="anchor-boundary"><Network size={25} /><strong>0</strong><span>schema lookups</span></div>
        </section>

        {error && <div className="error-banner"><AlertTriangle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}

        <section className="anchor-layout">
          <aside className="panel anchor-history">
            <div className="panel-head">
              <div><span className="step">H</span><h2>Retrieval runs</h2></div>
              <button className="anchor-icon-button" onClick={() => { setActive(null); setQuestion(EXAMPLE); setSelectedStage("normalization"); }} title="New retrieval run"><Plus size={15} /></button>
            </div>
            <div className="anchor-boundary-note"><Tags size={15} /><span>Entities and paths are targets. Values and operations only restrict or support those targets.</span></div>
            <div className="anchor-history-list">
              {runs.map((run) => (
                <button key={run.run_id} className={active?.run_id === run.run_id ? "active" : ""} onClick={() => void select(run.run_id)}>
                  <span className={`run-status status-${run.status}`}><i />{run.status}</span>
                  <small>{relativeTime(run.updated_at)}</small>
                  <strong>{run.question}</strong>
                  <span>{run.mode} · {run.retrieval_bundle?.targets.length ?? 0} targets</span>
                  {terminal.has(run.status) && <i className="anchor-delete" role="button" onClick={(event) => { event.stopPropagation(); void remove(run.run_id); }}><Trash2 size={12} /></i>}
                </button>
              ))}
              {!runs.length && <div className="empty-history">No extraction runs yet.</div>}
            </div>
          </aside>

          <div className="anchor-content">
            <section className="panel anchor-composer">
              <div className="panel-head">
                <div><span className="step">01</span><h2>Prepare schema retrieval concepts</h2></div>
                <label className="anchor-mode"><span>Mode</span><select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}><option value="auto">Auto</option><option value="llm">GPT semantic enrichment</option><option value="deterministic">Deterministic</option></select></label>
              </div>
              <div className="anchor-question-row">
                <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={5} placeholder="Enter a natural-language query" />
                <button disabled={busy || question.trim().length < 3} onClick={() => void start()}>{busy ? <LoaderCircle size={17} className="spin" /> : <Sparkles size={17} />} Prepare retrieval</button>
              </div>
              <div className="anchor-composer-foot"><span>6 persisted stages · no schema or MongoDB access</span><span>{question.length} characters</span></div>
            </section>

            {active ? (
              <section className="panel anchor-monitor">
                <div className="anchor-run-head">
                  <div><span className="eyebrow">RUN {active.run_id.slice(0, 8)} · {active.mode}</span><h2>{active.question}</h2></div>
                  <div className="anchor-progress"><span className={`run-status status-${active.status}`}><i />{active.status}</span><strong>{progress}%</strong><div><i style={{ width: `${progress}%` }} /></div></div>
                </div>
                {active.failure && <div className="error-banner embedded"><AlertTriangle size={15} />{active.failure}</div>}
                <div className="anchor-stage-grid">
                  <StageTimeline stages={active.stages} selected={selectedStage} onSelect={setSelectedStage} />
                  <article className="anchor-artifact">
                    <header><div><span className="eyebrow">INTERMEDIATE RESULT</span><strong>{LABELS[selectedStage] ?? selectedStage}</strong></div>{trace?.duration_ms != null && <span><Clock3 size={12} />{formatDuration(trace.duration_ms)}</span>}</header>
                    <div className="anchor-artifact-scroll"><StageArtifact run={active} stage={selectedStage} trace={trace} /></div>
                  </article>
                </div>
              </section>
            ) : (
              <section className="panel anchor-empty"><Network size={34} /><span className="eyebrow">RETRIEVAL BEFORE PLANNING</span><h2>Find only the schema evidence the question needs.</h2><p>Inspect primary targets, supporting fields, contextual restrictions, the retrieval graph, and prepared schema-search requests.</p></section>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function StageTimeline({ stages, selected, onSelect }: { stages: AnchorStageTrace[]; selected: string; onSelect: (stage: string) => void }) {
  return <aside className="anchor-timeline"><div className="anchor-timeline-title"><Activity size={13} /> Extraction trace</div>{stages.map((stage, index) => <button key={stage.stage} className={`${stage.status} ${selected === stage.stage ? "selected" : ""}`} onClick={() => onSelect(stage.stage)}><span className="anchor-stage-icon">{stage.status === "completed" ? <Check size={13} /> : stage.status === "running" ? <LoaderCircle size={13} className="spin" /> : stage.status === "failed" ? <X size={13} /> : <CircleDashed size={13} />}</span><span><strong>{LABELS[stage.stage] ?? stage.stage}</strong><small>{stage.summary || (stage.status === "pending" ? "Waiting" : stage.status)}</small></span>{stage.duration_ms != null && <i>{formatDuration(stage.duration_ms)}</i>}{index < stages.length - 1 && <b />}</button>)}</aside>;
}

function StageArtifact({ run, stage, trace }: { run: AnchorRunView; stage: string; trace?: AnchorStageTrace }) {
  if (!trace || !["completed", "failed"].includes(trace.status)) return <WaitingStage trace={trace} />;
  if (trace.status === "failed") return <Raw value={{ error: trace.error, artifact: trace.artifact }} />;
  if (stage === "normalization" && run.normalized_question) return <NormalizationView value={run.normalized_question} />;
  if (stage === "target_extraction" && run.target_extraction) return <ExtractionView title="Primary retrieval targets" subtitle="Only entities, requested fields, and requested derived concepts can become targets." anchors={run.target_extraction.anchors} relations={run.target_extraction.relations} notes={[...run.target_extraction.notes, ...run.target_extraction.unresolved_phrases.map((item) => `Unresolved phrase: ${item}`)]} deferred={run.target_extraction.deferred_plan_cues} />;
  if (stage === "support_inference" && run.support_inference) return <ExtractionView title="Supporting schema evidence" subtitle="Additional fields are introduced only when filters or derived outputs require them." anchors={run.support_inference.anchors} relations={run.support_inference.relations} notes={run.support_inference.notes} />;
  if (stage === "restriction_binding" && run.restriction_binding) return <RestrictionView values={run.restriction_binding.restrictions} deferred={run.restriction_binding.deferred_plan_cues} notes={run.restriction_binding.notes} />;
  if (stage === "retrieval_graph" && run.retrieval_graph) return <GraphView nodes={run.retrieval_graph.nodes} edges={run.retrieval_graph.edges} restrictions={run.retrieval_graph.restrictions} rootIds={run.retrieval_graph.root_anchor_ids} warnings={run.retrieval_graph.validation_warnings} />;
  if (stage === "retrieval_bundle" && run.retrieval_bundle) return <BundleView value={run.retrieval_bundle} />;
  return <Raw value={trace.artifact} />;
}

function WaitingStage({ trace }: { trace?: AnchorStageTrace }) {
  return <div className="anchor-waiting"><Clock3 size={25} /><h3>{trace ? LABELS[trace.stage] : "Stage"}</h3><p>{trace?.status === "running" ? "Processing this stage now." : "This stage has not started yet."}</p></div>;
}

function NormalizationView({ value }: { value: NormalizedQuestion }) {
  return <div className="anchor-stack"><Hero icon={<Braces size={20} />} label="Conservative normalized text" title={value.normalized_text} detail={`${value.clauses.length} clauses · ${value.scalars.length} scalars · ${value.cues.length} cues`} /><div className="anchor-two-column"><Section title="Clauses" count={value.clauses.length}>{value.clauses.map((item) => <div className="anchor-clause" key={item.clause_id}><code>{item.clause_id}</code><span>{item.text}</span></div>)}</Section><Section title="Scalars" count={value.scalars.length}><div className="anchor-chips">{value.scalars.map((item) => <span key={item.scalar_id}><code>{String(item.normalized)}</code>{item.scalar_type} · {item.context || "unscoped"}</span>)}</div></Section></div><Section title="Canonical cues" count={value.cues.length}><div className="anchor-cue-grid">{value.cues.map((cue) => <article key={cue.cue_id}><span>{cue.category}</span><strong>{cue.canonical}</strong><p>“{cue.surface}”</p><small>{cue.scope_hint}</small></article>)}</div></Section><Notes title="Semantic spans" values={value.semantic_spans} /><Raw value={value} /></div>;
}

function ExtractionView({ title, subtitle, anchors, relations, notes, deferred = [] }: { title: string; subtitle: string; anchors: TypedSemanticAnchor[]; relations: AnchorRelation[]; notes: string[]; deferred?: DeferredPlanCue[] }) {
  return <div className="anchor-stack"><Hero icon={<Tags size={20} />} label={title} title={`${anchors.length} retrieval targets`} detail={subtitle} /><AnchorCards values={anchors} />{relations.length > 0 && <RelationList values={relations} />}{deferred.length > 0 && <DeferredCues values={deferred} />}<Notes title="Extraction notes" values={notes} /></div>;
}

function AnchorCards({ values }: { values: TypedSemanticAnchor[] }) {
  return <div className="anchor-card-grid">{values.map((anchor) => <article className="anchor-card" key={anchor.anchor_id}><div><code>{anchor.anchor_id}</code><span className={`anchor-kind ${anchor.kind}`}>{anchor.kind.replaceAll("_", " ")}</span><strong>{Math.round(anchor.confidence * 100)}%</strong></div><h3>{anchor.canonical}</h3><p>{anchor.description}</p><footer><span>{anchor.retrieval_role}</span><span>{anchor.explicit ? "explicit" : "inferred"}</span>{anchor.output_requested && <span>requested output</span>}</footer>{anchor.expected_bson_types.length > 0 && <div className="anchor-chips small">{anchor.expected_bson_types.map((item) => <span key={item}>{item}</span>)}</div>}{anchor.aliases.length > 0 && <small className="anchor-alternatives">Aliases: {anchor.aliases.join(" · ")}</small>}{anchor.derivation_hints.length > 0 && <small className="anchor-alternatives">Evidence: {anchor.derivation_hints.join(" · ")}</small>}</article>)}</div>;
}

function RelationList({ values }: { values: AnchorRelation[] }) {
  return <Section title="Semantic relations" count={values.length}><div className="anchor-relations">{values.map((item) => <div key={item.relation_id}><code>{item.source_anchor_id}</code><span>{item.relation_type.replaceAll("_", " ")}</span><code>{item.target_anchor_id}</code><p>{item.description}</p></div>)}</div></Section>;
}

function RestrictionView({ values, deferred, notes }: { values: RetrievalRestriction[]; deferred: DeferredPlanCue[]; notes: string[] }) {
  return <div className="anchor-stack"><Hero icon={<Braces size={20} />} label="Context attached to targets" title={`${values.length} retrieval restrictions`} detail="Values and operation language constrain target scoring without becoming independent graph nodes." /><div className="anchor-restrictions">{values.map((item) => <article key={item.restriction_id}><div><span>{item.kind.replaceAll("_", " ")}</span><code>{item.retrieval_effect.replaceAll("_", " ")}</code></div><h3>{item.surface}</h3><p>{item.description}</p><footer><span>{item.operator ?? "context"}{item.normalized_value == null ? "" : ` · ${String(item.normalized_value)}`}</span><span>{item.anchor_ids.length} targets</span><strong>{Math.round(item.confidence * 100)}%</strong></footer><div className="anchor-chips small">{item.anchor_ids.map((id) => <span key={id}>{id}</span>)}</div></article>)}</div><DeferredCues values={deferred} /><Notes title="Binding policy" values={notes} /></div>;
}

function DeferredCues({ values }: { values: DeferredPlanCue[] }) {
  if (!values.length) return null;
  return <Section title="Deferred plan cues" count={values.length}><div className="anchor-deferred-cues">{values.map((item) => <article key={item.cue_id}><span>{item.kind.replaceAll("_", " ")}</span><strong>{item.surface}</strong><code>{item.canonical}</code><p>{item.reason}</p></article>)}</div></Section>;
}

const TREE_NODE_WIDTH = 176;
const TREE_NODE_HEIGHT = 68;
const TREE_COLUMN_GAP = 94;
const TREE_ROW_GAP = 30;

type TreePoint = {
  id: string;
  x: number;
  y: number;
  depth: number;
  parentId: string | null;
};

type TreeLayout = {
  points: Map<string, TreePoint>;
  roots: string[];
  treeEdgeIds: Set<string>;
  width: number;
  height: number;
};

function buildTreeLayout(nodes: TypedSemanticAnchor[], edges: AnchorRelation[], requestedRoots: string[]): TreeLayout {
  const nodeById = new Map(nodes.map((node) => [node.anchor_id, node]));
  const neighbors = new Map<string, Array<{ id: string; edge: AnchorRelation }>>();
  nodes.forEach((node) => neighbors.set(node.anchor_id, []));
  edges.forEach((edge) => {
    if (!nodeById.has(edge.source_anchor_id) || !nodeById.has(edge.target_anchor_id)) return;
    neighbors.get(edge.source_anchor_id)?.push({ id: edge.target_anchor_id, edge });
    neighbors.get(edge.target_anchor_id)?.push({ id: edge.source_anchor_id, edge });
  });
  neighbors.forEach((values) => values.sort((left, right) => {
    const leftName = nodeById.get(left.id)?.canonical ?? left.id;
    const rightName = nodeById.get(right.id)?.canonical ?? right.id;
    return leftName.localeCompare(rightName);
  }));

  const roots = [...new Set(requestedRoots.filter((id) => nodeById.has(id)))];
  const visited = new Set(roots);
  const parentById = new Map<string, string | null>(roots.map((id) => [id, null]));
  const depthById = new Map<string, number>(roots.map((id) => [id, 0]));
  const children = new Map<string, string[]>(nodes.map((node) => [node.anchor_id, []]));
  const treeEdgeIds = new Set<string>();

  const walk = (initial: string[]) => {
    const queue = [...initial];
    while (queue.length) {
      const current = queue.shift()!;
      for (const neighbor of neighbors.get(current) ?? []) {
        if (visited.has(neighbor.id)) continue;
        visited.add(neighbor.id);
        parentById.set(neighbor.id, current);
        depthById.set(neighbor.id, (depthById.get(current) ?? 0) + 1);
        children.get(current)?.push(neighbor.id);
        treeEdgeIds.add(neighbor.edge.relation_id);
        queue.push(neighbor.id);
      }
    }
  };

  if (roots.length) walk(roots);
  nodes
    .slice()
    .sort((left, right) => left.canonical.localeCompare(right.canonical))
    .forEach((node) => {
      if (visited.has(node.anchor_id)) return;
      roots.push(node.anchor_id);
      visited.add(node.anchor_id);
      parentById.set(node.anchor_id, null);
      depthById.set(node.anchor_id, 0);
      walk([node.anchor_id]);
    });

  let row = 0;
  let maxDepth = 0;
  const points = new Map<string, TreePoint>();
  const place = (id: string): number => {
    const childIds = children.get(id) ?? [];
    let center: number;
    if (!childIds.length) {
      center = row * (TREE_NODE_HEIGHT + TREE_ROW_GAP) + TREE_NODE_HEIGHT / 2 + 28;
      row += 1;
    } else {
      const childCenters = childIds.map(place);
      center = (childCenters[0] + childCenters[childCenters.length - 1]) / 2;
    }
    const depth = depthById.get(id) ?? 0;
    maxDepth = Math.max(maxDepth, depth);
    points.set(id, {
      id,
      x: depth * (TREE_NODE_WIDTH + TREE_COLUMN_GAP) + 42,
      y: center - TREE_NODE_HEIGHT / 2,
      depth,
      parentId: parentById.get(id) ?? null
    });
    return center;
  };
  roots.forEach((root, index) => {
    if (index > 0) row += 0.35;
    place(root);
  });

  return {
    points,
    roots,
    treeEdgeIds,
    width: Math.max(700, maxDepth * (TREE_NODE_WIDTH + TREE_COLUMN_GAP) + TREE_NODE_WIDTH + 84),
    height: Math.max(430, row * (TREE_NODE_HEIGHT + TREE_ROW_GAP) + 56)
  };
}

function GraphView({ nodes, edges, restrictions, rootIds, warnings }: { nodes: TypedSemanticAnchor[]; edges: AnchorRelation[]; restrictions: RetrievalRestriction[]; rootIds: string[]; warnings: string[] }) {
  const layout = useMemo(() => buildTreeLayout(nodes, edges, rootIds), [nodes, edges, rootIds]);
  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.anchor_id, node])), [nodes]);
  const [selectedId, setSelectedId] = useState(rootIds.find((id) => nodeById.has(id)) ?? nodes[0]?.anchor_id ?? "");
  const [view, setView] = useState({ scale: 1, x: 24, y: 24 });
  const [dragging, setDragging] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ pointerId: number; startX: number; startY: number; viewX: number; viewY: number } | null>(null);

  useEffect(() => {
    if (!nodeById.has(selectedId)) setSelectedId(rootIds.find((id) => nodeById.has(id)) ?? nodes[0]?.anchor_id ?? "");
  }, [nodeById, nodes, rootIds, selectedId]);

  const fitToView = useCallback(() => {
    const bounds = canvasRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const scale = Math.min(1.25, Math.max(0.24, Math.min((bounds.width - 42) / layout.width, (bounds.height - 42) / layout.height)));
    setView({
      scale,
      x: Math.max(18, (bounds.width - layout.width * scale) / 2),
      y: Math.max(18, (bounds.height - layout.height * scale) / 2)
    });
  }, [layout.height, layout.width]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(fitToView);
    return () => window.cancelAnimationFrame(frame);
  }, [fitToView]);

  const zoomAt = (factor: number, clientX?: number, clientY?: number) => {
    const bounds = canvasRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const focusX = clientX == null ? bounds.width / 2 : clientX - bounds.left;
    const focusY = clientY == null ? bounds.height / 2 : clientY - bounds.top;
    setView((current) => {
      const scale = Math.min(2.6, Math.max(0.22, current.scale * factor));
      const ratio = scale / current.scale;
      return {
        scale,
        x: focusX - (focusX - current.x) * ratio,
        y: focusY - (focusY - current.y) * ratio
      };
    });
  };

  const selected = nodeById.get(selectedId) ?? null;
  const selectedRelations = edges.filter((edge) => edge.source_anchor_id === selectedId || edge.target_anchor_id === selectedId);
  const selectedRestrictions = restrictions.filter((restriction) => restriction.anchor_ids.includes(selectedId));
  const rootSet = new Set(layout.roots);

  return <div className="anchor-stack anchor-graph-stack">
    <Hero icon={<Network size={20} />} label="Validated retrieval graph" title={`${nodes.length} targets · ${edges.length} relations · ${restrictions.length} restrictions`} detail="Only retrievable concepts are nodes. Restrictions remain attached metadata, and dashed links preserve additional structural context." />
    <section className="anchor-tree-workspace">
      <div className="anchor-tree-canvas" ref={canvasRef}>
        <div className="anchor-tree-toolbar" aria-label="Graph view controls">
          <button onClick={() => zoomAt(1.2)} title="Zoom in" aria-label="Zoom in"><ZoomIn size={15} /></button>
          <button onClick={() => zoomAt(1 / 1.2)} title="Zoom out" aria-label="Zoom out"><Minus size={15} /></button>
          <button onClick={fitToView} title="Fit graph to view" aria-label="Fit graph to view"><Maximize2 size={14} /></button>
          <span>{Math.round(view.scale * 100)}%</span>
        </div>
        <div className="anchor-tree-hint"><Network size={12} /> Scroll to zoom · drag to pan · select a node</div>
        <svg
          className={dragging ? "dragging" : ""}
          aria-label="Interactive typed semantic anchor tree"
          onWheel={(event) => { event.preventDefault(); zoomAt(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX, event.clientY); }}
          onPointerDown={(event) => {
            if (event.button !== 0) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            dragRef.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, viewX: view.x, viewY: view.y };
            setDragging(true);
          }}
          onPointerMove={(event) => {
            const drag = dragRef.current;
            if (!drag || drag.pointerId !== event.pointerId) return;
            setView((current) => ({ ...current, x: drag.viewX + event.clientX - drag.startX, y: drag.viewY + event.clientY - drag.startY }));
          }}
          onPointerUp={(event) => {
            if (dragRef.current?.pointerId !== event.pointerId) return;
            dragRef.current = null;
            setDragging(false);
            event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onPointerCancel={() => { dragRef.current = null; setDragging(false); }}
        >
          <defs>
            <marker id="anchor-tree-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 8 4 L 0 8 z" /></marker>
          </defs>
          <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>
            {edges.map((edge) => {
              const source = layout.points.get(edge.source_anchor_id);
              const target = layout.points.get(edge.target_anchor_id);
              if (!source || !target) return null;
              const sourceCenter = { x: source.x + TREE_NODE_WIDTH / 2, y: source.y + TREE_NODE_HEIGHT / 2 };
              const targetCenter = { x: target.x + TREE_NODE_WIDTH / 2, y: target.y + TREE_NODE_HEIGHT / 2 };
              const forward = sourceCenter.x <= targetCenter.x;
              const startX = sourceCenter.x + (forward ? TREE_NODE_WIDTH / 2 : -TREE_NODE_WIDTH / 2);
              const endX = targetCenter.x + (forward ? -TREE_NODE_WIDTH / 2 : TREE_NODE_WIDTH / 2);
              const bend = Math.max(42, Math.abs(endX - startX) * 0.45);
              const path = `M ${startX} ${sourceCenter.y} C ${startX + (forward ? bend : -bend)} ${sourceCenter.y}, ${endX - (forward ? bend : -bend)} ${targetCenter.y}, ${endX} ${targetCenter.y}`;
              const connected = edge.source_anchor_id === selectedId || edge.target_anchor_id === selectedId;
              return <g className={`anchor-tree-edge ${layout.treeEdgeIds.has(edge.relation_id) ? "tree" : "cross"} ${connected ? "connected" : ""}`} key={edge.relation_id}>
                <path d={path} markerEnd="url(#anchor-tree-arrow)" />
                {connected && view.scale >= 0.42 && <text x={(startX + endX) / 2} y={(sourceCenter.y + targetCenter.y) / 2 - 7}>{edge.relation_type.replaceAll("_", " ")}</text>}
              </g>;
            })}
            {[...layout.points.values()].map((point) => {
              const node = nodeById.get(point.id);
              if (!node) return null;
              const selectedNode = selectedId === point.id;
              return <g
                className={`anchor-tree-node ${node.kind} ${selectedNode ? "selected" : ""}`}
                data-graph-node="true"
                key={point.id}
                role="button"
                tabIndex={0}
                aria-label={`${node.kind}: ${node.canonical}`}
                aria-pressed={selectedNode}
                transform={`translate(${point.x} ${point.y})`}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => setSelectedId(point.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedId(point.id);
                  }
                }}
              >
                <rect width={TREE_NODE_WIDTH} height={TREE_NODE_HEIGHT} rx="9" />
                <rect className="anchor-tree-node-accent" width="5" height={TREE_NODE_HEIGHT} rx="3" />
                <text className="anchor-tree-node-kind" x="16" y="18">{node.kind.replaceAll("_", " ")}</text>
                <text className="anchor-tree-node-title" x="16" y="39">{truncate(node.canonical, 25)}</text>
                <text className="anchor-tree-node-meta" x="16" y="56">{node.anchor_id} · {Math.round(node.confidence * 100)}%</text>
                {rootSet.has(point.id) && <g className="anchor-tree-root" transform={`translate(${TREE_NODE_WIDTH - 42} 9)`}><rect width="32" height="14" rx="7" /><text x="16" y="10">ROOT</text></g>}
              </g>;
            })}
          </g>
        </svg>
      </div>
      <aside className="anchor-tree-inspector">
        {selected ? <>
          <header><span className={`anchor-kind ${selected.kind}`}>{selected.kind.replaceAll("_", " ")}</span><strong>{Math.round(selected.confidence * 100)}%</strong></header>
          <code>{selected.anchor_id}</code>
          <h3>{selected.canonical}</h3>
          <p className="anchor-tree-surface">“{selected.surface}”</p>
          <p>{selected.description}</p>
          <dl>
            <div><dt>Retrieval role</dt><dd>{selected.retrieval_role}</dd></div>
            <div><dt>Evidence</dt><dd>{selected.source} · {selected.explicit ? "explicit" : "inferred"}</dd></div>
            <div><dt>Source span</dt><dd>{selected.start == null || selected.end == null ? "Implicit" : `${selected.start}–${selected.end}`}</dd></div>
            <div><dt>Requested output</dt><dd>{selected.output_requested ? "Yes" : "Supporting evidence"}</dd></div>
          </dl>
          {selected.expected_bson_types.length > 0 && <div className="anchor-tree-detail-group"><strong>Expected BSON types</strong><div className="anchor-chips small">{selected.expected_bson_types.map((value) => <span key={value}>{value}</span>)}</div></div>}
          {selected.aliases.length > 0 && <div className="anchor-tree-detail-group"><strong>Aliases</strong><p>{selected.aliases.join(" · ")}</p></div>}
          {selected.parent_hints.length > 0 && <div className="anchor-tree-detail-group"><strong>Parent context</strong><p>{selected.parent_hints.join(" · ")}</p></div>}
          {selected.derivation_hints.length > 0 && <div className="anchor-tree-detail-group"><strong>Supporting evidence</strong><p>{selected.derivation_hints.join(" · ")}</p></div>}
          <div className="anchor-tree-detail-group"><strong>Attached restrictions <span>{selectedRestrictions.length}</span></strong><div className="anchor-tree-restriction-list">{selectedRestrictions.map((restriction) => <article key={restriction.restriction_id}><span>{restriction.kind.replaceAll("_", " ")}</span><strong>{restriction.surface}</strong><p>{restriction.operator ?? restriction.retrieval_effect}{restriction.normalized_value == null ? "" : ` · ${String(restriction.normalized_value)}`}</p></article>)}{!selectedRestrictions.length && <small>No attached restrictions.</small>}</div></div>
          <div className="anchor-tree-detail-group"><strong>Connected relations <span>{selectedRelations.length}</span></strong><div className="anchor-tree-relation-list">{selectedRelations.map((edge) => {
            const outgoing = edge.source_anchor_id === selectedId;
            const peerId = outgoing ? edge.target_anchor_id : edge.source_anchor_id;
            const peer = nodeById.get(peerId);
            return <button key={edge.relation_id} onClick={() => setSelectedId(peerId)}><span>{outgoing ? "→" : "←"} {edge.relation_type.replaceAll("_", " ")}</span><strong>{peer?.canonical ?? peerId}</strong></button>;
          })}{!selectedRelations.length && <small>No connected relations.</small>}</div></div>
        </> : <div className="anchor-tree-no-selection"><Network size={24} /><p>Select a node to inspect its grounding evidence.</p></div>}
      </aside>
    </section>
    <div className="anchor-tree-legend"><span><i className="tree" />Tree relation</span><span><i className="cross" />Cross-link</span><span><i className="root" />Component root</span><strong>{layout.roots.length} {layout.roots.length === 1 ? "tree" : "trees"}</strong></div>
    <Notes title="Graph warnings" values={warnings} danger />
  </div>;
}

function BundleView({ value }: { value: NonNullable<AnchorRunView["retrieval_bundle"]> }) {
  const targetNames = new Map(value.targets.map((item) => [item.id, item.canonical]));
  return <div className="anchor-stack"><section className="anchor-bundle-hero"><CheckCircle2 size={24} /><div><span className="eyebrow">MINIMAL RETRIEVALBUNDLE</span><h2>Ready for schema scoring</h2><p>{value.targets.length} targets · {value.relations.length} relations · {value.value_constraints.length} value constraints</p></div><strong>4<small>top-level fields</small></strong></section><div className="anchor-card-grid">{value.targets.map((target) => <article className="anchor-card" key={target.id}><div><code>{target.id}</code><span className={`anchor-kind ${target.kind}`}>{target.kind.replaceAll("_", " ")}</span><strong>{target.role}</strong></div><h3>{target.canonical}</h3><footer>{target.parent_hints.map((item) => <span key={item}>{item}</span>)}</footer>{target.expected_types.length > 0 && <div className="anchor-chips small">{target.expected_types.map((item) => <span key={item}>{item}</span>)}</div>}{target.aliases.length > 0 && <small className="anchor-alternatives">Aliases: {target.aliases.join(" · ")}</small>}</article>)}</div>{value.relations.length > 0 && <Section title="Minimal relations" count={value.relations.length}><div className="anchor-relations">{value.relations.map((relation, index) => <div key={`${relation.source}-${relation.target}-${index}`}><code>{targetNames.get(relation.source) ?? relation.source}</code><span>{relation.type.replaceAll("_", " ")}</span><code>{targetNames.get(relation.target) ?? relation.target}</code><p>Structural link retained for coherent subtree selection.</p></div>)}</div></Section>}{value.value_constraints.length > 0 && <Section title="Value constraints" count={value.value_constraints.length}><div className="anchor-bundle-constraints">{value.value_constraints.map((constraint, index) => <article key={`${constraint.kind}-${index}`}><span>{constraint.kind}</span><strong>{constraint.operator ?? "context"} · {String(constraint.value)}</strong><p>{constraint.target_ids.map((id) => targetNames.get(id) ?? id).join(" · ")}</p></article>)}</div></Section>}<Raw value={value} /></div>;
}

function Hero({ icon, label, title, detail }: { icon: React.ReactNode; label: string; title: string; detail: string }) {
  return <section className="anchor-artifact-hero"><span>{icon}</span><div><small className="eyebrow">{label}</small><h2>{title}</h2><p>{detail}</p></div></section>;
}

function Section({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return <section className="anchor-section"><header><strong>{title}</strong><span>{count}</span></header>{children}</section>;
}

function Notes({ title, values, danger = false }: { title: string; values: string[]; danger?: boolean }) {
  if (!values.length) return null;
  return <section className={`anchor-notes ${danger ? "danger" : ""}`}><strong>{title}</strong><ul>{values.map((value) => <li key={value}>{value}</li>)}</ul></section>;
}

function Raw({ value }: { value: unknown }) {
  return <details className="anchor-raw"><summary><FileJson2 size={13} /> Raw JSON</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}

function formatDuration(value: number) {
  return value < 1000 ? `${Math.round(value)}ms` : `${(value / 1000).toFixed(1)}s`;
}

function truncate(value: string, limit: number) {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}

function relativeTime(value: string) {
  const minutes = Math.floor((Date.now() - new Date(value).getTime()) / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours}h` : `${Math.floor(hours / 24)}d`;
}
