export type MethodSpec = {
  id: string;
  title: string;
  family: "baseline" | "sag";
  description: string;
  supports_self_consistency: boolean;
};

export type TrackSpec = {
  id: "canonical" | "robustness";
  field: "NLQ" | "NLQ_colloquial";
  title: string;
  description: string;
};

export type DatasetSummary = {
  available: boolean;
  task_count: number;
  database_count: number;
  databases?: string[];
  tasks_per_database: Record<string, number>;
  robustness_task_count?: number;
  balanced_110?: boolean;
};

export type Catalog = {
  methods: MethodSpec[];
  tracks: TrackSpec[];
  dataset: DatasetSummary;
  upstream: {
    available: boolean;
    commit: string | null;
    catalog_matches_upstream: boolean | null;
  };
};

export type Health = {
  status: "ready" | "degraded";
  mongodb: {
    available: boolean;
    message: string;
    database_count: number;
    collection_count: number;
  };
  dataset: DatasetSummary;
  upstream: { available: boolean; commit: string | null };
  provider: {
    configured: boolean;
    ready: boolean;
    stub: boolean;
    model: string;
    reasoning_effort: string;
  };
  embedding_provider?: {
    configured: boolean;
    ready: boolean;
    model: string;
    base_url: string;
  };
  defaults: { concurrency: number };
  execution: { available: boolean; message: string };
};

export type RunStatus =
  | "queued"
  | "running"
  | "pausing"
  | "paused"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed";

export type RunView = {
  id: string;
  mode: "benchmark" | "custom_query";
  name: string;
  status: RunStatus;
  method_ids: string[];
  tracks: string[];
  database_ids: string[];
  model: string;
  reasoning_effort: string;
  concurrency: number;
  execute_custom_query: boolean;
  total_items: number;
  pending_items: number;
  retrying_items: number;
  running_items: number;
  succeeded_items: number;
  failed_items: number;
  cancelled_items: number;
  progress: number;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
};

export type WorkItem = {
  id: number;
  run_id: string;
  ordinal: number;
  method_id: string;
  track: string;
  db_id: string;
  record_id: number | string | null;
  question: string;
  payload: Record<string, unknown>;
  status: "pending" | "retrying" | "running" | "succeeded" | "failed" | "cancelled";
  attempt: number;
  generation_attempt: number;
  retry_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
};

export type RunCreate = {
  mode: "benchmark" | "custom_query";
  method_ids: string[];
  tracks?: string[];
  database_id?: string;
  question?: string;
  execute_custom_query?: boolean;
  concurrency: number;
  model?: string;
  reasoning_effort?: "none" | "low" | "medium" | "high" | "xhigh" | "max";
};

export type Scores = Record<string, number>;

export type OutcomeDistribution = {
  total: number;
  counts: Record<string, number>;
  fractions: Record<string, number>;
};

export type SliceBucket = { record_count: number; scores: Scores };

export type EvaluationReport = {
  status: "ok" | "partial" | "failed";
  record_count: number;
  release_record_count: number;
  outcome_buckets_order: string[];
  systems: Record<string, {
    record_count: number;
    scores: Scores;
    outcome_distribution: OutcomeDistribution;
  }>;
  slice_aggregates: Record<string, Record<string, SliceBucket>>;
  system_slice_aggregates: Record<string, Record<string, Record<string, SliceBucket>>>;
};

export type EvaluationResults = {
  run_id: string;
  status: "pending" | "running" | "completed" | "failed";
  tracks: string[];
  artifacts: Record<string, Record<string, string>>;
  reports: Record<string, EvaluationReport>;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
};

export type ResultRecord = {
  system_id: string;
  db_id: string;
  record_id: string | number;
  outcome: string;
  status: string;
  metrics: Scores;
  diagnostics?: Record<string, unknown>;
};

export type ResultRecordPage = {
  total: number;
  offset: number;
  limit: number;
  items: ResultRecord[];
};

export type AnchorKind =
  | "entity"
  | "field"
  | "derived_concept";

export type TypedSemanticAnchor = {
  anchor_id: string;
  kind: AnchorKind;
  surface: string;
  canonical: string;
  description: string;
  retrieval_role: "primary" | "supporting";
  expected_bson_types: string[];
  aliases: string[];
  parent_hints: string[];
  output_requested: boolean;
  derivation_hints: string[];
  explicit: boolean;
  source: "rule" | "llm" | "inferred" | "merged";
  start: number | null;
  end: number | null;
  confidence: number;
};

export type AnchorRelation = {
  relation_id: string;
  source_anchor_id: string;
  target_anchor_id: string;
  relation_type: string;
  description: string;
  confidence: number;
  source: string;
};

export type NormalizedQuestion = {
  original_text: string;
  normalized_text: string;
  locale: string;
  timezone: string;
  reference_time: string;
  clauses: Array<{ clause_id: string; text: string; start: number; end: number }>;
  scalars: Array<{
    scalar_id: string;
    surface: string;
    normalized: unknown;
    scalar_type: string;
    context: string;
    start: number;
    end: number;
  }>;
  cues: Array<{
    cue_id: string;
    surface: string;
    category: string;
    canonical: string;
    scope_hint: string;
    start: number;
    end: number;
    source: string;
  }>;
  semantic_spans: string[];
  notes: string[];
};

export type RetrievalRestriction = {
  restriction_id: string;
  kind: "temporal" | "value" | "comparison" | "cardinality" | "role_hint" | "eligibility" | "scope";
  surface: string;
  canonical: string;
  description: string;
  anchor_ids: string[];
  operator: string | null;
  normalized_value: string | number | boolean | null;
  retrieval_effect: "filter_value" | "type_hint" | "role_hint" | "support_requirement" | "relation_scope";
  source: string;
  start: number | null;
  end: number | null;
  confidence: number;
};

export type DeferredPlanCue = {
  cue_id: string;
  surface: string;
  kind: "sorting" | "tie_policy" | "presentation";
  canonical: string;
  reason: string;
  start: number | null;
  end: number | null;
};

export type RetrievalBundleTarget = {
  id: string;
  kind: AnchorKind;
  role: "primary" | "supporting";
  canonical: string;
  aliases: string[];
  parent_hints: string[];
  expected_types: string[];
};

export type RetrievalBundleRelation = {
  source: string;
  target: string;
  type: "belongs_to" | "requires_connection" | "scoped_with" | "supports" | "derived_from";
};

export type RetrievalValueConstraint = {
  kind: "temporal" | "value" | "comparison";
  target_ids: string[];
  operator: string | null;
  value: string | number | boolean | null;
};

export type AnchorStageTrace = {
  stage: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  summary: string;
  artifact: unknown;
  error: string | null;
};

export type AnchorRunView = {
  run_id: string;
  question: string;
  mode: "auto" | "llm" | "deterministic";
  locale: string;
  timezone: string;
  status: "created" | "running" | "completed" | "failed";
  created_at: string;
  updated_at: string;
  model_id: string;
  stages: AnchorStageTrace[];
  normalized_question: NormalizedQuestion | null;
  target_extraction: {
    anchors: TypedSemanticAnchor[];
    relations: AnchorRelation[];
    unresolved_phrases: string[];
    deferred_plan_cues: DeferredPlanCue[];
    notes: string[];
  } | null;
  support_inference: {
    anchors: TypedSemanticAnchor[];
    relations: AnchorRelation[];
    notes: string[];
  } | null;
  restriction_binding: {
    restrictions: RetrievalRestriction[];
    deferred_plan_cues: DeferredPlanCue[];
    notes: string[];
  } | null;
  retrieval_graph: {
    nodes: TypedSemanticAnchor[];
    edges: AnchorRelation[];
    restrictions: RetrievalRestriction[];
    root_anchor_ids: string[];
    connected_components: string[][];
    validation_warnings: string[];
  } | null;
  retrieval_bundle: {
    question: string;
    targets: RetrievalBundleTarget[];
    relations: RetrievalBundleRelation[];
    value_constraints: RetrievalValueConstraint[];
  } | null;
  failure: string | null;
};

export type IndexStageTrace = {
  stage: string;
  status: "pending" | "running" | "completed" | "failed" | "paused" | "cancelled";
  progress: number;
  summary: string;
  artifact: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
};

export type SchemaIndexRunView = {
  run_id: string;
  index_id: string;
  database_id: string;
  status: "created" | "running" | "pausing" | "paused" | "completed" | "failed" | "cancelled";
  created_at: string;
  updated_at: string;
  embedding_model: string;
  stages: IndexStageTrace[];
  node_count: number;
  collection_count: number;
  value_count: number;
  dynamic_key_count: number;
  array_path_count: number;
  reference_edge_count: number;
  embedding_count: number;
  schema_hash: string | null;
  artifact_dir: string | null;
  failure: string | null;
};

export type PruningStageTrace = {
  stage: string;
  status: "pending" | "running" | "completed" | "failed";
  progress: number;
  summary: string;
  artifact: unknown;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  error: string | null;
};

export type SearchRequest = {
  target_id: string;
  kind: string;
  role: string;
  query_text: string;
  context_text: string;
  expected_types: string[];
  value_constraints: Array<Record<string, unknown>>;
};

export type PathCandidate = {
  target_id: string;
  path_id: string;
  collection: string;
  path: string;
  score: number;
  signals: {
    lexical: number;
    semantic: number;
    name: number;
    context: number | null;
    type: number | null;
    value: number | null;
    exact_contains: boolean | null;
    range_contains: boolean | null;
  };
  binding: Record<string, unknown> | null;
};

export type SchemaReferenceEdge = {
  source_id: string;
  target_id: string;
  source_distinct_count: number;
  matched_distinct_count: number;
  overlap: number;
  inferred_by: "declared" | "strict_inference";
};

export type RelationshipEvaluation = {
  relation_index: number;
  relation_type: string;
  source_target_id: string;
  target_target_id: string;
  source_path_id: string;
  target_path_id: string;
  score: number;
  distance: number | null;
  common_ancestor_id: string | null;
  connector_node_ids: string[];
  reference_edges: SchemaReferenceEdge[];
  resolved: boolean;
};

export type CandidateCombination = {
  rank: number;
  selections: Record<string, string>;
  local_score: number;
  relationship_score: number;
  final_score: number;
  relationship_evaluations: RelationshipEvaluation[];
  connector_node_ids: string[];
  reference_edges: SchemaReferenceEdge[];
};

export type PrunedSchema = {
  combination_rank: number;
  score: number;
  nodes: Array<{
    id: string;
    collection: string;
    path: string;
    parent_id: string | null;
    types: string[];
    role: "target" | "connector" | "ancestor";
    target_ids: string[];
    binding: Record<string, unknown> | null;
  }>;
  reference_edges: SchemaReferenceEdge[];
};

export type SchemaPruningRunView = {
  run_id: string;
  bundle_run_id: string;
  index_id: string;
  database_id: string;
  status: "created" | "running" | "completed" | "failed";
  created_at: string;
  updated_at: string;
  config: {
    primary_top_k: number;
    supporting_top_k: number;
    beam_width: number;
    final_top_k: number;
    alternative_margin: number;
    local_weight: number;
    relationship_weight: number;
  };
  stages: PruningStageTrace[];
  search_requests: SearchRequest[];
  candidates_by_target: Record<string, PathCandidate[]>;
  relationship_evaluations: RelationshipEvaluation[];
  combinations: CandidateCombination[];
  pruned_schemas: PrunedSchema[];
  unresolved_target_ids: string[];
  failure: string | null;
};
