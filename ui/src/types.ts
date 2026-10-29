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

export interface DecisionDetail {
  explanation: string | null;
  missing_relationships: { from_table: string; to_table: string }[];
  clarification_question: string | null;
  interpretations: string[];
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
  model_detail: DecisionDetail | null;
  runtime_outcome: RuntimeOutcome;
  runtime_reason: string | null;
  provider: string | null;
  model: string | null;
  model_context: GovernedContext | null;
  model_context_hash: string | null;
  proposed_sql: string | null;
  repaired_from_sql: string | null;
  executed_sql: string | null;
  sql_changes: string[];
  rows: Record<string, unknown>[];
  columns: string[];
  row_count: number;
  truncated: boolean;
  duration_ms: number | null;
  created_at: string;
  trace: RunTrace;
  actor: string;
  connection_id: string;
  follow_up_of?: string | null;
  results_retained: boolean;
}

export type RunSummary = Pick<RunRecord, "run_id" | "trace_id" | "question" | "preset_id" | "proposal_source" | "model_decision" | "runtime_outcome" | "runtime_reason" | "row_count" | "duration_ms" | "created_at" | "actor">;

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
  row_estimate: number | null;
}

export interface SourceDatabase {
  name: string;
  engine: string;
  server_version: string | null;
  schema_name: string;
  connected: boolean;
}

export interface SchemaResponse {
  title: string;
  description: string;
  database: SourceDatabase | null;
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

export type ConnectorKind = "postgresql" | "mysql" | "sqlserver" | "trino" | "snowflake" | "bigquery";

export interface ConnectorField {
  name: string;
  label: string;
  kind: "text" | "number" | "password" | "select" | "textarea";
  required: boolean;
  placeholder: string | null;
  options: string[];
  default: string | null;
}

export interface ConnectorType {
  kind: ConnectorKind;
  label: string;
  description: string;
  available: boolean;
  installable: boolean;
  unavailable_reason: string | null;
  default_port: number | null;
  fields: ConnectorField[];
}

export interface ConnectionSummary {
  id: string;
  name: string;
  kind: ConnectorKind;
  host: string;
  port: number | null;
  database: string;
  username: string;
  schema_name: string;
  ssl_mode: string;
  options: Record<string, string>;
  has_password: boolean;
  built_in: boolean;
  governed: boolean;
  queryable: boolean;
  created_by: string;
  created_at: string | null;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string | null;
}

export interface ConnectionTestResult {
  ok: boolean;
  message: string;
  error_kind: string | null;
  field: string | null;
  detail: string | null;
  server_version: string | null;
  latency_ms: number | null;
  table_count: number | null;
  can_write: boolean | null;
}

export interface IntrospectedTable {
  name: string;
  row_estimate: number | null;
  columns: { name: string; type: string; nullable: boolean }[];
}

export interface DriverInstallResult {
  ok: boolean;
  message: string;
  packages: string[];
  log_tail: string;
}

export interface RelationshipDeclaration {
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
}

export interface DeclaredRelationship extends RelationshipDeclaration {
  id: string;
  connection_id: string;
  created_by: string;
  created_at: string;
}

export interface RelationshipSuggestion extends RelationshipDeclaration {
  reason: string;
  match_rate: number | null;
}

export interface ConnectionRelationships {
  foreign_keys: RelationshipDeclaration[];
  declared: DeclaredRelationship[];
  suggestions: RelationshipSuggestion[];
}

export interface ValueColumn {
  table: string;
  column: string;
  values: string[];
  complete: boolean;
  patterns: string[];
  shared: boolean;
}

export interface ConnectionValues {
  available: boolean;
  enabled: boolean;
  excluded: string[];
  updated_by: string | null;
  updated_at: string | null;
  columns: ValueColumn[];
}

export type ChartType = "number" | "bar" | "column" | "line" | "area" | "pie" | "table";

export interface CardLayout {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface CardDisplay {
  format: "auto" | "number" | "currency" | "percent";
  decimals: number | null;
  currency: string;
  sort: "query" | "value_desc" | "value_asc" | "label";
  limit: number | null;
  compare: "none" | "second_row" | "target";
  target: number | null;
  higher_is_better: boolean;
}

export interface ReportCard {
  id: string;
  kind: "query" | "text" | "heading";
  title: string;
  note: string;
  question: string;
  run_id: string | null;
  chart: ChartType;
  dimension: string | null;
  measure: string | null;
  display?: CardDisplay;
  layout: CardLayout;
  sql?: string | null;
  basis?: Record<string, string>;
}

export interface CardRefresh {
  run: RunRecord;
  changed_tables: string[];
  applied_filters: string[];
  skipped_filters: string[];
}

export type FilterKind = "values" | "date_range";

export interface ReportFilter {
  id: string;
  label: string;
  table: string;
  column: string;
  kind: FilterKind;
}

/** One reader's pick for one filter; dates are YYYY-MM-DD. */
export interface FilterChoice {
  values?: string[];
  start?: string | null;
  end?: string | null;
}

export interface FilterColumn {
  table: string;
  column: string;
  type: string;
  kinds: FilterKind[];
}

export interface FilterOptions {
  values: string[];
  truncated: boolean;
  run_id: string;
}

export type ReportVisibility = "private" | "workspace";
export type ReportRole = "owner" | "editor" | "viewer";

export interface ReportAccess {
  visibility: ReportVisibility;
  editors: string[];
  viewers: string[];
}

export type RefreshSchedule = "off" | "hourly" | "daily" | "weekly";

export interface Report extends ReportAccess {
  id: string;
  connection_id: string;
  title: string;
  description: string;
  cards: ReportCard[];
  filters: ReportFilter[];
  role: ReportRole;
  schedule: RefreshSchedule;
  next_refresh_at: string | null;
  created_by: string;
  created_at: string;
  updated_by: string;
  updated_at: string;
}

export interface SnapshotCard {
  run_id: string;
  outcome: RuntimeOutcome;
  reason: string | null;
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  truncated: boolean;
  /** The tables this card reads changed since: the rows are held back. */
  withheld: boolean;
}

export interface SnapshotSummary {
  id: string;
  report_id: string;
  taken_at: string;
  taken_by: string;
  scheduled: boolean;
  card_count: number;
}

export interface Snapshot extends Omit<SnapshotSummary, "card_count"> {
  cards: Record<string, SnapshotCard>;
}

export interface ReportSummary {
  id: string;
  connection_id: string;
  title: string;
  description: string;
  card_count: number;
  owner: string;
  visibility: ReportVisibility;
  role: ReportRole;
  updated_by: string;
  updated_at: string;
}
