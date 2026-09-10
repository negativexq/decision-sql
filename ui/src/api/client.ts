import type { Preset, RunRecord, RunSummary, SchemaResponse } from "../types";

const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string; api: string; database: string }>("/api/health"),
  presets: () => request<Preset[]>("/api/playground/presets"),
  query: (question: string, preset_id?: string) =>
    request<RunRecord>("/api/playground/query", {
      method: "POST",
      body: JSON.stringify({ question, ...(preset_id ? { preset_id } : {}) }),
    }),
  runs: () => request<{ runs: RunSummary[] }>("/api/runs"),
  run: (id: string) => request<RunRecord>(`/api/runs/${encodeURIComponent(id)}`),
  schema: () => request<SchemaResponse>("/api/schema"),
};
