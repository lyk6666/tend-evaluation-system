import type {
  Catalog,
  EvaluationResults,
  Health,
  ResultRecordPage,
  RunCreate,
  RunView,
  WorkItem
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", "Content-Type": "application/json", ...init?.headers }
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  catalog: () => request<Catalog>("/api/catalog"),
  runs: () => request<RunView[]>("/api/runs"),
  run: (runId: string) => request<RunView>(`/api/runs/${runId}`),
  items: (runId: string, limit = 100) =>
    request<WorkItem[]>(`/api/runs/${runId}/items?limit=${limit}`),
  createRun: (payload: RunCreate) =>
    request<RunView>("/api/runs", { method: "POST", body: JSON.stringify(payload) }),
  pause: (runId: string) =>
    request<RunView>(`/api/runs/${runId}/pause`, { method: "POST" }),
  resume: (runId: string) =>
    request<RunView>(`/api/runs/${runId}/resume`, { method: "POST" }),
  cancel: (runId: string) =>
    request<RunView>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  results: (runId: string) => request<EvaluationResults>(`/api/runs/${runId}/results`),
  resultRecords: (
    runId: string,
    params: { track: string; system_id?: string; outcome?: string; limit?: number }
  ) => {
    const query = new URLSearchParams({ track: params.track });
    if (params.system_id) query.set("system_id", params.system_id);
    if (params.outcome) query.set("outcome", params.outcome);
    query.set("limit", String(params.limit ?? 50));
    return request<ResultRecordPage>(`/api/runs/${runId}/results/records?${query}`);
  },
  evaluate: (runId: string) =>
    request<EvaluationResults>(`/api/runs/${runId}/evaluate`, { method: "POST" })
};
