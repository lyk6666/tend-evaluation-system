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
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  attempt: number;
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
};
