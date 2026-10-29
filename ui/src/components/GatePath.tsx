import type { RunTrace, StageStatus, TraceStage } from "../types";

/** The user-meaningful subset of trace stages, in lifecycle order, with short names. */
const GATES: { names: string[]; label: string }[] = [
  { names: ["context"], label: "Context" },
  { names: ["generation", "proposal_replay"], label: "Model" },
  { names: ["decision_admission"], label: "Decision" },
  { names: ["sql_parse"], label: "Parse" },
  { names: ["global_policy"], label: "Policy" },
  { names: ["execution_authority"], label: "Access" },
  { names: ["grain_safety"], label: "Grain" },
  { names: ["value_check"], label: "Values" },
  { names: ["explain"], label: "Plan" },
  { names: ["cost_gate"], label: "Cost" },
  { names: ["execution"], label: "Run" },
];

function pick(stages: TraceStage[], names: string[]): TraceStage | undefined {
  const matches = stages.filter((stage) => names.includes(stage.name));
  return matches.find((stage) => stage.status !== "SKIPPED") ?? matches[0];
}

const statusText: Record<StageStatus, string> = {
  PASS: "passed",
  REJECTED: "stopped the query",
  FAILED: "failed",
  SKIPPED: "skipped",
};

export function GatePath({ trace, onSelect }: { trace: RunTrace | null; onSelect?: (stage: TraceStage) => void }) {
  return (
    <ol className="gates" aria-label="Checks this question went through">
      {GATES.map((gate) => {
        const stage = trace ? pick(trace.stages, gate.names) : undefined;
        // A finished trace without this stage means the run never reached it.
        const status = stage?.status.toLowerCase() ?? (trace ? "skipped" : "pending");
        return (
          <li key={gate.label} className={`gate gate-${status}`}>
            <button
              type="button"
              disabled={!stage || !onSelect}
              onClick={() => stage && onSelect?.(stage)}
              title={stage ? `${gate.label} ${statusText[stage.status]}${stage.reason ? `: ${stage.reason}` : ""}` : gate.label}
            >
              <span className="gate-bar" aria-hidden="true" />
              <span className="gate-label">{gate.label}</span>
              <span className="visually-hidden">{stage ? statusText[stage.status] : "pending"}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
