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
