import { useEffect, useState } from "react";
import { api } from "../api/client";
import { formatCount, formatMs, runOutcome } from "../format";
import type { RunSummary } from "../types";

type Scope = "all" | "mine";
type Outcome = "" | "EXECUTED" | "AUTHORITY_REJECTED" | "POLICY_REJECTED" | "NOT_ENTERED" | "EXECUTION_ERROR";

export function Audit({ actor, refreshKey, onOpen }: { actor: string; refreshKey: number; onOpen: (run: RunSummary) => void }) {
  const [scope, setScope] = useState<Scope>("all");
  const [outcome, setOutcome] = useState<Outcome>("");
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [durable, setDurable] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    void api
      .runs({ actor: scope === "mine" ? actor || "anonymous" : undefined, outcome: outcome || undefined })
      .then((data) => {
        setRuns(data.runs);
        setDurable(data.durable);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [scope, outcome, actor, refreshKey]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Audit log</h1>
          <p className="page-sub">Every question, who asked it, the SQL that was proposed and what the checks decided.</p>
        </div>
        <div className="filters">
          <div className="segmented" role="group" aria-label="Whose questions">
            <button type="button" aria-pressed={scope === "all"} onClick={() => setScope("all")}>Everyone</button>
            <button type="button" aria-pressed={scope === "mine"} onClick={() => setScope("mine")}>Mine</button>
          </div>
          <label className="select">
            <span className="visually-hidden">Outcome</span>
            <select value={outcome} onChange={(event) => setOutcome(event.target.value as Outcome)}>
              <option value="">All outcomes</option>
              <option value="EXECUTED">Answered</option>
              <option value="NOT_ENTERED">Declined by model</option>
              <option value="AUTHORITY_REJECTED">Stopped: no access</option>
              <option value="POLICY_REJECTED">Stopped: policy</option>
              <option value="EXECUTION_ERROR">Query failed</option>
            </select>
          </label>
        </div>
      </header>

      {!durable && (
        <div className="callout callout-warning">
          <strong>This log is not being kept.</strong> Set DECISION_SQL_METADATA_DATABASE_URL on the API so records survive a restart.
        </div>
      )}
      {error && <div className="callout callout-danger" role="alert">{error}</div>}

      {runs && runs.length > 0 && (
        <div className="table-wrap audit-wrap">
          <table className="data-table audit-table">
            <thead>
              <tr>
                <th>When</th>
                <th>User</th>
                <th>Question</th>
                <th>Outcome</th>
                <th className="num">Rows</th>
                <th className="num">Time</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const result = runOutcome(run);
                return (
                  <tr key={run.run_id} tabIndex={0} onClick={() => onOpen(run)} onKeyDown={(event) => event.key === "Enter" && onOpen(run)}>
                    <td className="muted">{new Date(run.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</td>
                    <td className="audit-actor" title={run.actor}>{run.actor}</td>
                    <td className="audit-question" title={run.question}>{run.question}</td>
                    <td><span className={`dot dot-${result.tone}`} aria-hidden="true" /> {result.label}</td>
                    <td className="num">{run.runtime_outcome === "EXECUTED" ? formatCount(run.row_count) : ""}</td>
                    <td className="num muted">{formatMs(run.duration_ms)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {runs && runs.length === 0 && (
        <div className="empty-panel">
          <strong>No records match</strong>
          <p>{scope === "mine" ? "You have not asked anything yet under this name." : "Questions appear here as soon as they are run."}</p>
        </div>
      )}
    </div>
  );
}
