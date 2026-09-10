export type Decision =
  | "ANSWER"
  | "NEEDS_CLARIFICATION"
  | "BLOCKED_AUTHORITY"
  | "BLOCKED_POLICY"
  | "GENERATION_FAILED";

export type RuntimeOutcome =
  | "EXECUTED"
  | "AUTHORITY_REJECTED"
  | "POLICY_REJECTED"
  | "RUNTIME_REJECTED"
  | "EXECUTION_ERROR"
  | "NOT_ENTERED"
  | "GENERATION_ERROR";

export type StageStatus = "PASS" | "REJECTED" | "FAILED" | "SKIPPED";

export interface TraceStage {
  name: string;
  label: string;
  status: StageStatus;
  duration_ms: number | null;
  reason: string | null;
  metadata: Record<string, unknown>;
}

export interface TraceEvent {
  event_id: string;
  event_type: string;
  at: string;
  message: string;
  stage: string | null;
}

export interface RunTrace {
  trace_id: string;
  run_id: string;
  status: string;
  started_at: string;
  duration_ms: number | null;
  stages: TraceStage[];
  events: TraceEvent[];
}

export interface RunRecord {
  run_id: string;
  trace_id: string;
  question: string;
  preset_id: string | null;
  decision: Decision;
  runtime_outcome: RuntimeOutcome;
  reason_code: string | null;
  sql: string | null;
  rows: Record<string, unknown>[];
  columns: string[];
  row_count: number;
  truncated: boolean;
  duration_ms: number | null;
  created_at: string;
  trace: RunTrace;
}

export type RunSummary = Pick<RunRecord, "run_id" | "trace_id" | "question" | "preset_id" | "decision" | "runtime_outcome" | "reason_code" | "row_count" | "duration_ms" | "created_at">;

export interface Preset {
  id: string;
  label: string;
  description: string;
  category: string;
  question: string;
}

export interface SchemaColumn {
  name: string;
  type: string;
  description: string;
  queryable: boolean;
  primary_key: boolean;
}

export interface SchemaEntity {
  name: string;
  description: string;
  columns: SchemaColumn[];
  relationships: { source_column: string; target_table: string; target_column: string }[];
}

export interface SchemaResponse {
  title: string;
  entities: SchemaEntity[];
}
