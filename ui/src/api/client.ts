import type {
  ConnectionRelationships,
  ConnectionValues,
  ConnectionSummary,
  ConnectionTestResult,
  ConnectorType,
  DeclaredRelationship,
  DriverInstallResult,
  IntrospectedTable,
  Preset,
  RelationshipDeclaration,
  RunRecord,
  RunSummary,
  SchemaResponse,
} from "../types";

const base = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";
const ACTOR_KEY = "decision-sql.actor";

/** Self-declared identity sent with every request and recorded in the audit log. */
export function getActor(): string {
  try {
    return localStorage.getItem(ACTOR_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setActor(value: string): void {
  try {
    localStorage.setItem(ACTOR_KEY, value);
  } catch {
    // Storage can be unavailable in private windows; the value then lasts for the session only.
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const actor = getActor();
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(actor ? { "X-Decision-Actor": actor } : {}),
      ...(options?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    let detail = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
      else if (Array.isArray(parsed.detail)) {
        detail = parsed.detail.map((item: { msg?: string }) => item.msg ?? "").filter(Boolean).join("; ");
      }
    } catch {
      // Non-JSON error bodies are shown as returned.
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export type ConnectionInput = Record<string, string | number | Record<string, string>>;

export const api = {
  health: () => request<{ status: string; api: string; database: string }>("/api/health"),
  presets: () => request<Preset[]>("/api/playground/presets"),
  query: (question: string, preset_id?: string, connection_id = "governed") =>
    request<RunRecord>("/api/playground/query", {
      method: "POST",
      body: JSON.stringify({ question, connection_id, ...(preset_id ? { preset_id } : {}) }),
    }),
  runs: (filters: { actor?: string; outcome?: string } = {}) => {
    const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value) as [string, string][]);
    return request<{ runs: RunSummary[]; durable: boolean }>(`/api/runs${params.size ? `?${params}` : ""}`);
  },
  run: (id: string) => request<RunRecord>(`/api/runs/${encodeURIComponent(id)}`),
  schema: () => request<SchemaResponse>("/api/schema"),
  connectors: () => request<ConnectorType[]>("/api/connectors"),
  installDriver: (kind: string) =>
    request<DriverInstallResult>(`/api/connectors/${encodeURIComponent(kind)}/install`, { method: "POST" }),
  connections: () => request<ConnectionSummary[]>("/api/connections"),
  testConnection: (config: ConnectionInput) =>
    request<ConnectionTestResult>("/api/connections/test", { method: "POST", body: JSON.stringify(config) }),
  createConnection: (config: ConnectionInput) =>
    request<{ connection: ConnectionSummary; test: ConnectionTestResult; saved: boolean }>("/api/connections", {
      method: "POST",
      body: JSON.stringify(config),
    }),
  retestConnection: (id: string) =>
    request<ConnectionTestResult>(`/api/connections/${encodeURIComponent(id)}/test`, { method: "POST" }),
  connectionTables: (id: string) => request<IntrospectedTable[]>(`/api/connections/${encodeURIComponent(id)}/tables`),
  deleteConnection: (id: string) => request<void>(`/api/connections/${encodeURIComponent(id)}`, { method: "DELETE" }),
  relationships: (id: string) =>
    request<ConnectionRelationships>(`/api/connections/${encodeURIComponent(id)}/relationships`),
  declareRelationship: (id: string, declaration: RelationshipDeclaration) =>
    request<DeclaredRelationship>(`/api/connections/${encodeURIComponent(id)}/relationships`, {
      method: "POST",
      body: JSON.stringify(declaration),
    }),
  removeRelationship: (id: string, relationshipId: string) =>
    request<void>(
      `/api/connections/${encodeURIComponent(id)}/relationships/${encodeURIComponent(relationshipId)}`,
      { method: "DELETE" },
    ),
  values: (id: string) => request<ConnectionValues>(`/api/connections/${encodeURIComponent(id)}/values`),
  setValues: (id: string, enabled: boolean, excluded: string[]) =>
    request<ConnectionValues>(`/api/connections/${encodeURIComponent(id)}/values`, {
      method: "PUT",
      body: JSON.stringify({ enabled, excluded }),
    }),
};
