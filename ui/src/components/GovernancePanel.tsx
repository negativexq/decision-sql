import { formatMs, humanize } from "../format";
import type { RunRecord, TraceStage } from "../types";

export function GovernancePanel({ run, stage, onSelect }: { run: RunRecord; stage: TraceStage | null; onSelect: (stage: TraceStage) => void }) {
  const context = run.model_context;
  const active = stage ?? run.trace.stages.find((item) => item.status === "REJECTED" || item.status === "FAILED") ?? null;
  return (
    <div className="governance">
      <section>
        <h3 className="panel-title">Checks</h3>
        <ul className="stage-list">
          {run.trace.stages.map((item) => (
            <li key={item.name}>
              <button
                type="button"
                className={active?.name === item.name ? "stage-row selected" : "stage-row"}
                onClick={() => onSelect(item)}
              >
                <span className={`dot dot-${toneFor(item.status)}`} aria-hidden="true" />
                <span className="stage-name">{item.label}</span>
                <span className="stage-status">{humanize(item.status)}</span>
                <span className="stage-time">{formatMs(item.duration_ms)}</span>
              </button>
            </li>
          ))}
        </ul>
        {active && (
          <div className="stage-detail">
            <strong>{active.label}</strong>
            <p>{active.reason ? humanize(active.reason) : "Completed without a rejection."}</p>
            {Object.keys(active.metadata).length > 0 && (
              <dl>
                {Object.entries(active.metadata).map(([key, value]) => (
                  <div key={key}>
                    <dt>{humanize(key)}</dt>
                    <dd>{String(value)}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        )}
      </section>

      <section>
        <h3 className="panel-title">What the model could see</h3>
        {context ? (
          <>
            <p className="panel-note">
              {run.proposal_source === "LIVE_MODEL"
                ? "Only these tables and columns were sent to the model for this question."
                : "This is a replayed scenario: no model was called, and the stored proposal was checked against this scope."}
            </p>
            <ul className="scope-list">
              {context.schema_catalog.map((entity) => (
                <li key={entity.entity_id}>
                  <strong>{entity.physical_table}</strong>
                  <span>
                    {context.attributes
                      .filter((attribute) => attribute.entity_id === entity.entity_id)
                      .map((attribute) => attribute.physical_column_or_path)
                      .join(", ") || "No columns selected"}
                  </span>
                </li>
              ))}
            </ul>
            {context.authorized_relationships.length > 0 && (
              <>
                <h4 className="panel-subtitle">Allowed joins</h4>
                <ul className="join-list">
                  {context.authorized_relationships.map((relationship) => (
                    <li key={relationship.relationship_id}>
                      <code>{relationship.source_attribute_id.split(":").slice(-2).join(".")}</code>
                      <span aria-label="joins to">=</span>
                      <code>{relationship.target_attribute_id.split(":").slice(-2).join(".")}</code>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p className="panel-meta">
              Context fingerprint <code>{run.model_context_hash?.slice(0, 12) ?? "none"}</code>
            </p>
          </>
        ) : (
          <p className="panel-note">The question could not be matched to any table, so nothing was sent to the model.</p>
        )}
      </section>
    </div>
  );
}

function toneFor(status: TraceStage["status"]): string {
  if (status === "PASS") return "success";
  if (status === "REJECTED") return "danger";
  if (status === "FAILED") return "danger";
  return "neutral";
}
