export type Decision =
  | "ANSWER"
  | "NEEDS_CLARIFICATION"
  | "BLOCKED_AUTHORITY"
  | "BLOCKED_POLICY";

export type ProposalSource = "LIVE_MODEL" | "SAFETY_REPLAY" | "RUNTIME_POLICY_REPLAY";

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
  proposal_source: ProposalSource;
  replay_notice: string | null;
  model_decision: Decision | null;
  model_reason_code: string | null;
  runtime_outcome: RuntimeOutcome;
  runtime_reason: string | null;
  provider: string | null;
  model: string | null;
  proposed_sql: string | null;
  rows: Record<string, unknown>[];
  columns: string[];
  row_count: number;
  truncated: boolean;
  duration_ms: number | null;
  created_at: string;
  trace: RunTrace;
}

export type RunSummary = Pick<RunRecord, "run_id" | "trace_id" | "question" | "preset_id" | "proposal_source" | "model_decision" | "runtime_outcome" | "runtime_reason" | "row_count" | "duration_ms" | "created_at">;

export interface Preset {
  id: string;
  label: string;
  description: string;
  category: string;
  question: string;
  mode: ProposalSource;
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
