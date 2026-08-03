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
  | "attribute"
  | "measure"
  | "stored_literal"
  | "query_constant"
  | "temporal"
  | "operation"
  | "comparison"
  | "output"
  | "relationship"
  | "grouping"
  | "sort"
  | "tie_policy"
  | "quantifier"
  | "negation";

export type TypedSemanticAnchor = {
  anchor_id: string;
  kind: AnchorKind;
  surface: string;
  canonical: string;
  description: string;
  semantic_role: string;
  expected_bson_types: string[];
  explicit: boolean;
  source: "rule" | "llm" | "inferred" | "merged";
  start: number | null;
  end: number | null;
  confidence: number;
  alternatives: string[];
  retrieval_required: boolean;
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

export type AnchorAmbiguity = {
  ambiguity_id: string;
  text: string;
  ambiguity_type: string;
  anchor_ids: string[];
  interpretations: string[];
  recommended_interpretation: string;
  reason: string;
  blocking: boolean;
  confidence: number;
};

export type RetrievalSpecification = {
  specification_id: string;
  anchor_ids: string[];
  search_kind: "path" | "value_path_group" | "type_compatible_path" | "relationship" | "structure" | "none";
  query_terms: string[];
  semantic_query: string;
  expected_bson_types: string[];
  structural_constraints: string[];
  required: boolean;
  rationale: string;
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
  deterministic_extraction: {
    anchors: TypedSemanticAnchor[];
    relations: AnchorRelation[];
    unresolved_phrases: string[];
    notes: string[];
  } | null;
  semantic_extraction: {
    anchors: TypedSemanticAnchor[];
    relations: AnchorRelation[];
    notes: string[];
  } | null;
  ambiguity_extraction: {
    ambiguities: AnchorAmbiguity[];
    notes: string[];
  } | null;
  anchor_graph: {
    nodes: TypedSemanticAnchor[];
    edges: AnchorRelation[];
    root_anchor_ids: string[];
    connected_components: string[][];
    validation_warnings: string[];
  } | null;
  retrieval_specifications: {
    specifications: RetrievalSpecification[];
    notes: string[];
  } | null;
  anchor_bundle: {
    question: string;
    normalized_question: NormalizedQuestion;
    anchors: TypedSemanticAnchor[];
    relations: AnchorRelation[];
    ambiguities: AnchorAmbiguity[];
    retrieval_specifications: RetrievalSpecification[];
    coverage_score: number;
    ready_for_retrieval: boolean;
    validation_warnings: string[];
  } | null;
  failure: string | null;
};
