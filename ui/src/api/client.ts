import type {
  CardRefresh,
  ConnectionNotes,
  ConnectionRelationships,
  ConnectionSummary,
  ConnectionTestResult,
  ConnectionValues,
  ConnectorType,
  DeclaredRelationship,
  DriverInstallResult,
  FilterChoice,
  FilterColumn,
  FilterOptions,
  IntrospectedTable,
  Preset,
  RefreshSchedule,
  RelationshipDeclaration,
  Report,
  ReportAccess,
  ReportCard,
  ReportFilter,
  ReportSummary,
  RunRecord,
  RunSummary,
  SchemaResponse,
  Snapshot,
  SnapshotSummary,
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
  reports: () => request<ReportSummary[]>("/api/reports"),
  report: (id: string) => request<Report>(`/api/reports/${encodeURIComponent(id)}`),
  createReport: (title: string, connection_id: string) =>
    request<Report>("/api/reports", { method: "POST", body: JSON.stringify({ title, connection_id }) }),
  saveReport: (id: string, body: { title: string; description: string; cards: ReportCard[]; filters: ReportFilter[] }) =>
    request<Report>(`/api/reports/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({
        ...body,
        // The server pins each card's SQL and basis from its run; it does not accept them from here.
        cards: body.cards.map(({ sql: _sql, basis: _basis, ...card }) => card),
      }),
    }),
  shareReport: (id: string, access: ReportAccess) =>
    request<Report>(`/api/reports/${encodeURIComponent(id)}/access`, { method: "PUT", body: JSON.stringify(access) }),
  setReportSchedule: (id: string, schedule: RefreshSchedule) =>
    request<Report>(`/api/reports/${encodeURIComponent(id)}/schedule`, { method: "PUT", body: JSON.stringify({ schedule }) }),
  snapshots: (id: string) => request<SnapshotSummary[]>(`/api/reports/${encodeURIComponent(id)}/snapshots`),
  takeSnapshot: (id: string) =>
    request<SnapshotSummary>(`/api/reports/${encodeURIComponent(id)}/snapshots`, { method: "POST" }),
  snapshot: (id: string, snapshotId: string) =>
    request<Snapshot>(`/api/reports/${encodeURIComponent(id)}/snapshots/${encodeURIComponent(snapshotId)}`),
  deleteReport: (id: string) => request<void>(`/api/reports/${encodeURIComponent(id)}`, { method: "DELETE" }),
  askForCard: (id: string, question: string) =>
    request<RunRecord>(`/api/reports/${encodeURIComponent(id)}/ask`, { method: "POST", body: JSON.stringify({ question }) }),
  followUpCard: (id: string, cardId: string, question: string) =>
    request<RunRecord>(`/api/reports/${encodeURIComponent(id)}/cards/${encodeURIComponent(cardId)}/follow-up`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
  refreshCard: (id: string, cardId: string, filters: Record<string, FilterChoice> = {}) =>
    request<CardRefresh>(`/api/reports/${encodeURIComponent(id)}/cards/${encodeURIComponent(cardId)}/refresh`, {
      method: "POST",
      body: JSON.stringify({ filters }),
    }),
  filterColumns: (id: string) => request<FilterColumn[]>(`/api/reports/${encodeURIComponent(id)}/filter-columns`),
  filterOptions: (id: string, filterId: string) =>
    request<FilterOptions>(`/api/reports/${encodeURIComponent(id)}/filters/${encodeURIComponent(filterId)}/options`, {
      method: "POST",
    }),
  acceptCard: (id: string, cardId: string) =>
    request<Report>(`/api/reports/${encodeURIComponent(id)}/cards/${encodeURIComponent(cardId)}/accept`, {
      method: "POST",
    }),
  values: (id: string) => request<ConnectionValues>(`/api/connections/${encodeURIComponent(id)}/values`),
  setValues: (id: string, enabled: boolean, excluded: string[]) =>
    request<ConnectionValues>(`/api/connections/${encodeURIComponent(id)}/values`, {
      method: "PUT",
      body: JSON.stringify({ enabled, excluded }),
    }),
  notes: (id: string) => request<ConnectionNotes>(`/api/connections/${encodeURIComponent(id)}/notes`),
  setNotes: (id: string, notes: { table: string; column: string; description: string }[]) =>
    request<ConnectionNotes>(`/api/connections/${encodeURIComponent(id)}/notes`, {
      method: "PUT",
      body: JSON.stringify({ notes }),
    }),
};
