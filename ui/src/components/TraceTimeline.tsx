import type { RunTrace, TraceStage } from "../types";
import { StatusBadge } from "./StatusBadge";

export function TraceTimeline({ trace, onStage }: { trace: RunTrace; onStage: (stage: TraceStage) => void }) {
  return (
    <div className="trace-list">
      {trace.stages.map((stage) => (
        <button type="button" className="trace-row" key={stage.name} onClick={() => onStage(stage)} aria-label={`Inspect ${stage.label} stage`}>
          <span className={`trace-dot ${stage.status.toLowerCase()}`} />
          <span className="trace-name">{stage.label}</span>
          <StatusBadge value={stage.status} />
          <span className="trace-duration">{stage.duration_ms == null ? "—" : `${stage.duration_ms.toFixed(1)} ms`}</span>
          <span className="trace-chevron" aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
