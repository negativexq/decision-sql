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
  model_context: GovernedContext | null;
  model_context_hash: string | null;
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
  description: string;
  entities: SchemaEntity[];
}

export interface GovernedContext {
  context_profile: "GOVERNED_CONTEXT_V1";
  database_id: string;
  context_scope: "REQUEST_BOUNDED";
  schema_catalog: GovernedEntity[];
  attributes: GovernedAttribute[];
  authorized_relationships: GovernedRelationship[];
  metrics: GovernedMetric[];
  business_rules: GovernedBusinessRule[];
  temporal_rules: GovernedTemporalRule[];
  policy: GovernedPolicy;
}

export interface GovernedEntity {
  entity_id: string;
  physical_table: string;
  description: string;
  queryable: boolean;
}

export interface GovernedAttribute {
  attribute_id: string;
  entity_id: string;
  physical_column_or_path: string;
  semantic_type: string;
  description: string;
  queryable: boolean;
  primary_key: boolean;
  foreign_key_entity_id: string | null;
  foreign_key_attribute_id: string | null;
}

export interface GovernedRelationship {
  relationship_id: string;
  source_entity_id: string;
  source_attribute_id: string;
  target_entity_id: string;
  target_attribute_id: string;
  authorized: true;
}

export interface GovernedMetric { metric_id: string; name: string; description: string; }
export interface GovernedBusinessRule { rule_id: string; description: string; }
export interface GovernedTemporalRule { temporal_rule_id: string; description: string; }
export interface GovernedPolicy { policy_id: string; mode: "READ_ONLY"; allowed_statement_types: string[]; }
