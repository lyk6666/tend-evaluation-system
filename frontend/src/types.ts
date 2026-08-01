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
    model: string;
    reasoning_effort: string;
  };
  defaults: { concurrency: number };
};

