import type { Decision, RuntimeOutcome, StageStatus } from "../types";

export function StatusBadge({ value }: { value: Decision | RuntimeOutcome | StageStatus | string }) {
  const tone = value.includes("REJECT") || value.includes("ERROR") || value === "FAILED"
    ? "danger"
    : value.includes("BLOCK") || value.includes("CLAR") || value === "SKIPPED"
      ? "warning"
      : value === "PASS" || value === "EXECUTED" || value === "ANSWER"
        ? "success"
        : "neutral";
  return <span className={`status-badge ${tone}`}>{value.replaceAll("_", " ")}</span>;
}
