import type { Decision, RunSummary, RuntimeOutcome } from "./types";

export type Tone = "success" | "warning" | "danger" | "neutral";

export interface Outcome {
  label: string;
  tone: Tone;
}

/** One user-facing outcome per run, combining model decision and runtime result. */
export function runOutcome(run: Pick<RunSummary, "model_decision" | "runtime_outcome">): Outcome {
  const decision: Decision | null = run.model_decision;
  const runtime: RuntimeOutcome = run.runtime_outcome;
  if (runtime === "EXECUTED") return { label: "Answered", tone: "success" };
  if (decision === "NEEDS_CLARIFICATION") return { label: "Needs clarification", tone: "warning" };
  if (decision === "BLOCKED_AUTHORITY") return { label: "Declined: no access", tone: "warning" };
  if (decision === "BLOCKED_POLICY") return { label: "Declined: policy", tone: "warning" };
  if (runtime === "AUTHORITY_REJECTED") return { label: "Stopped: no access", tone: "danger" };
  if (runtime === "POLICY_REJECTED") return { label: "Stopped: policy", tone: "danger" };
  if (runtime === "RUNTIME_REJECTED") return { label: "Stopped by runtime", tone: "danger" };
  if (runtime === "EXECUTION_ERROR") return { label: "Query failed", tone: "danger" };
  if (runtime === "GENERATION_ERROR") return { label: "Model error", tone: "danger" };
  return { label: "Not run", tone: "neutral" };
}

export function humanize(code: string): string {
  const text = code.replaceAll("_", " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function formatMs(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1000) return `${(value / 1000).toFixed(1)} s`;
  return `${Math.round(value)} ms`;
}

export function formatCount(value: number | null | undefined): string {
  if (value == null) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

export function relativeTime(iso: string): string {
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

/** PostgreSQL numerics arrive as JSON strings; treat both forms as numbers. */
export function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "" && /^-?\d+(\.\d+)?(e[+-]?\d+)?$/i.test(value.trim())) {
    return Number(value);
  }
  return null;
}

export function formatNumber(value: number): string {
  const digits = Number.isInteger(value) ? 0 : 2;
  return new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
}

function formatDecimal(value: number): string {
  return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);
}

export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  const number = toNumber(value);
  // Decimal columns arrive as strings like "2880.00"; keep their scale so a column reads consistently.
  if (number !== null) return typeof value === "string" && value.includes(".") ? formatDecimal(number) : formatNumber(number);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export const KIND_LABEL: Record<string, string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  sqlserver: "SQL Server",
  trino: "Trino",
  snowflake: "Snowflake",
  bigquery: "BigQuery",
};
