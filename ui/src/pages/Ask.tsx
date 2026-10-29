import { type ReactNode, useEffect, useRef, useState } from "react";
import { GatePath } from "../components/GatePath";
import { GovernancePanel } from "../components/GovernancePanel";
import { HowComputed } from "../components/HowComputed";
import { SourcePicker } from "../components/SourcePicker";
import { ResultChart, chartShape } from "../components/ResultChart";
import { ResultTable } from "../components/ResultTable";
import { SqlViewer } from "../components/SqlViewer";
import { formatCount, formatMs, humanize, runOutcome } from "../format";
import { AddToReport, type AddTarget } from "../components/AddToReport";
import type {
  ChartType,
  ConditionEdit,
  ConnectionSummary,
  Preset,
  ProgressEvent,
  Report,
  ReportSummary,
  RunRecord,
  RunTrace,
  TraceStage,
} from "../types";

type Tab = "table" | "chart" | "checks";

/** What a running question has reached, from the server's progress events. */
export interface Progress {
  question: string;
  startedAt: number;
  stages: TraceStage[];
  // The stage that has started and not finished, if the server said so.
  working: string | null;
  modelCalls: number;
}

export function nextProgress(progress: Progress, event: ProgressEvent): Progress {
  if (event.type === "working") {
    return {
      ...progress,
      working: event.name,
      modelCalls: progress.modelCalls + (event.name === "generation" ? 1 : 0),
    };
  }
  const stage: TraceStage = {
    name: event.name,
    label: event.label,
    status: event.status,
    duration_ms: event.duration_ms,
    reason: event.reason,
    metadata: {},
  };
  return {
    ...progress,
    stages: [...progress.stages.filter((item) => item.name !== event.name), stage],
    working: progress.working === event.name ? null : progress.working,
  };
}

/** A run whose question and query a follow-up can build on. */
function canFollow(run: RunRecord | undefined): run is RunRecord {
  return Boolean(run?.proposed_sql) && (run?.proposal_source === "LIVE_MODEL" || run?.proposal_source === "EDITED_QUERY");
}

export function Ask({
  thread,
  presets,
  progress,
  error,
  onSubmit,
  onEdit,
  editing,
  focusKey,
  connections,
  sourceId,
  onSourceChange,
  onManageConnections,
  reports,
  onAddToReport,
  onOpenReport,
}: {
  reports: ReportSummary[];
  onAddToReport: (run: RunRecord, target: AddTarget, chart: ChartType) => Promise<Report>;
  onOpenReport: (id: string) => void;
  thread: RunRecord[];
  presets: Preset[];
  progress: Progress | null;
  error: string | null;
  onSubmit: (question: string, presetId?: string, followUpOf?: string) => void;
  onEdit: (run: RunRecord, edits: ConditionEdit[]) => void;
  editing: string | null;
  focusKey: number;
  connections: ConnectionSummary[];
  sourceId: string;
  onSourceChange: (id: string) => void;
  onManageConnections: () => void;
}) {
  const loading = progress !== null || editing !== null;
  const source = connections.find((item) => item.id === sourceId) ?? connections.find((item) => item.governed);
  // The server marks sources that have the full policy, read-only and cost-gate path.
  const queryable = !source || source.queryable;
  // Example questions are written for the governed demo schema.
  const showStarters = !source || source.governed;
  const last = thread[thread.length - 1];
  const end = useRef<HTMLDivElement>(null);

  // Keep the newest turn in view as the thread grows.
  useEffect(() => {
    if (thread.length > 1 || progress) end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [thread.length, progress?.question]);

  return (
    <div className="ask">
      {thread.length === 0 && !progress && (
        <Composer
          key={focusKey}
          disabled={loading || !queryable}
          loading={loading}
          connections={connections}
          source={source}
          onSourceChange={onSourceChange}
          onManageConnections={onManageConnections}
          onSubmit={(question) => onSubmit(question)}
        />
      )}

      {!queryable && source && (
        <div className="callout callout-warning" role="status">
          <strong>{source.name} is connected, but questions can't run on it yet.</strong> Its tables can be
          browsed under Connections. Questions run on PostgreSQL sources, where every query passes the
          policy, read-only and cost checks.
        </div>
      )}

      {thread.map((run, index) =>
        index < thread.length - 1 ? (
          <EarlierTurn key={run.run_id} run={run} />
        ) : (
          <RunResult
            key={run.run_id}
            run={run}
            editing={editing === run.run_id}
            onEdit={loading ? undefined : (edits) => onEdit(run, edits)}
            onRerun={() => onSubmit(run.question, run.preset_id ?? undefined)}
            onAsk={(text) => onSubmit(text)}
            onManageConnections={onManageConnections}
            addToReport={
              <AddToReport
                run={run}
                reports={reports}
                onAdd={(target, chart) => onAddToReport(run, target, chart)}
                onOpenReport={onOpenReport}
              />
            }
          />
        ),
      )}

      {progress && <Pending progress={progress} />}

      {error && <div className="callout callout-danger" role="alert"><strong>The request did not complete.</strong> {error}</div>}

      {thread.length > 0 && !progress && (
        <Composer
          key={`follow-${last?.run_id}-${focusKey}`}
          disabled={loading || !queryable}
          loading={loading}
          connections={connections}
          source={source}
          onSourceChange={onSourceChange}
          onManageConnections={onManageConnections}
          followUp={canFollow(last) ? last : undefined}
          onSubmit={(question, followUpOf) => onSubmit(question, undefined, followUpOf)}
        />
      )}

      {thread.length === 0 && !progress && showStarters && (
        <section className="starters" aria-label="Example questions">
          <h2 className="section-title">Try a question</h2>
          <ul>
            {presets.map((preset) => (
              <li key={preset.id}>
                <button type="button" disabled={!queryable} onClick={() => onSubmit(preset.question, preset.id)}>
                  <span className="starter-question">{preset.question}</span>
                  <span className="starter-note">{preset.description}</span>
                  {preset.mode !== "LIVE_MODEL" && <span className="tag">Replay</span>}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      <div ref={end} />
    </div>
  );
}

function Composer({
  disabled,
  loading,
  connections,
  source,
  onSourceChange,
  onManageConnections,
  followUp,
  onSubmit,
}: {
  disabled: boolean;
  loading: boolean;
  connections: ConnectionSummary[];
  source: ConnectionSummary | undefined;
  onSourceChange: (id: string) => void;
  onManageConnections: () => void;
  // The answer a question here builds on, unless the user lets go of it.
  followUp?: RunRecord;
  onSubmit: (question: string, followUpOf?: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const [building, setBuilding] = useState(Boolean(followUp));
  const input = useRef<HTMLTextAreaElement>(null);
  const following = building && followUp !== undefined;

  useEffect(() => {
    // A follow-up box under an answer waits for the reader instead of taking focus.
    if (!followUp) input.current?.focus();
  }, [followUp]);

  const submit = () => {
    if (question.trim() && !disabled) onSubmit(question.trim(), following ? followUp.run_id : undefined);
  };

  return (
    <form
      className={followUp ? "composer composer-follow" : "composer"}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      {following && (
        <div className="follow-chip">
          <span>
            Builds on <q>{followUp.question}</q>
          </span>
          <button type="button" className="link" onClick={() => setBuilding(false)}>
            Ask a new question instead
          </button>
        </div>
      )}
      <label className="visually-hidden" htmlFor={followUp ? "follow-up" : "question"}>
        {following ? "Follow-up question" : "Question"}
      </label>
      <textarea
        id={followUp ? "follow-up" : "question"}
        ref={input}
        rows={2}
        value={question}
        placeholder={
          following
            ? "Ask a follow-up, e.g. split it by month"
            : "Ask a question about your data, e.g. total order revenue by status"
        }
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-bar">
        {following ? (
          <span className="composer-source">{source?.name}</span>
        ) : (
          <SourcePicker
            connections={connections}
            selected={source?.id ?? "governed"}
            onSelect={onSourceChange}
            onManage={onManageConnections}
          />
        )}
        <span className="hint">Enter to run, Shift + Enter for a new line</span>
        <button type="submit" className="button primary" disabled={!question.trim() || disabled}>
          {loading ? "Running" : following ? "Ask follow-up" : "Run question"}
        </button>
      </div>
    </form>
  );
}

function Pending({ progress }: { progress: Progress }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - progress.startedAt) / 1000));
  const trace = { stages: progress.stages } as RunTrace;
  const latest = progress.stages[progress.stages.length - 1];
  let status: string;
  if (progress.working === "generation") {
    status = progress.modelCalls > 1 ? `The model is working, call ${progress.modelCalls}` : "The model is writing the query";
  } else if (!latest) {
    status = "Reading the catalog";
  } else if (latest.name === "context") {
    status = "Building the governed context";
  } else if (["execution_connection", "explain", "cost_gate"].includes(latest.name)) {
    status = "Running the query read-only";
  } else {
    status = `${latest.label} ${latest.status === "PASS" ? "passed" : latest.status.toLowerCase()}`;
  }
  return (
    <section className="result" aria-busy="true">
      <header className="result-head">
        <h1 className="result-question">{progress.question}</h1>
        <p className="result-meta progress-line" role="status" aria-live="polite">
          <span className="progress-dot" aria-hidden="true" />
          <span>{status}</span>
          <span className="progress-time">{seconds} s</span>
        </p>
      </header>
      <GatePath trace={trace} live />
    </section>
  );
}

/** An earlier question in the thread: its line of explanation, with the answer one click away. */
function EarlierTurn({ run }: { run: RunRecord }) {
  const outcome = runOutcome(run);
  return (
    <details className="turn">
      <summary>
        <span className="turn-question">{run.question}</span>
        {run.edited_from && <span className="tag">Edited</span>}
        <span className={`pill pill-${outcome.tone}`}>{outcome.label}</span>
      </summary>
      <RunResult run={run} onRerun={() => undefined} onAsk={() => undefined} onManageConnections={() => undefined} readOnly />
    </details>
  );
}

function RunResult({
  run,
  onRerun,
  onAsk,
  onManageConnections,
  onEdit,
  editing = false,
  readOnly = false,
  addToReport,
}: {
  // The control that pins this answer to a report, for the newest answer only.
  addToReport?: ReactNode;
  run: RunRecord;
  onRerun: () => void;
  onAsk: (question: string) => void;
  onManageConnections: () => void;
  // Change a condition of the query and run it again without the model.
  onEdit?: (edits: ConditionEdit[]) => void;
  editing?: boolean;
  // An earlier turn of a thread: shown as it was, with nothing to act on.
  readOnly?: boolean;
}) {
  const outcome = runOutcome(run);
  const executed = run.runtime_outcome === "EXECUTED" && run.results_retained;
  const chartable = executed && chartShape(run.columns, run.rows) !== null;
  const [tab, setTab] = useState<Tab>(executed ? (chartable && run.rows.length > 1 ? "chart" : "table") : "checks");
  const [stage, setStage] = useState<TraceStage | null>(null);
  const [queryOpen, setQueryOpen] = useState(run.runtime_outcome !== "EXECUTED" && Boolean(run.proposed_sql));
  // When the runtime changed the logic, the query that ran is the runtime's text, not the model's.
  const changed = Boolean(run.executed_sql) && (run.sql_changes ?? []).length > 0;
  const shownSql = changed ? run.executed_sql : run.proposed_sql;
  // Either check may find a filter value the column does not store: before the run, or after an empty result.
  const unmatched = (run.trace?.stages ?? [])
    .filter((item) => (item.name === "value_check" || item.name === "result_check") && item.reason === "VALUE_NOT_STORED")
    .map((item) => String(item.metadata?.unmatched ?? ""))
    .filter(Boolean)
    .join(", ");
  const tied = (run.trace?.stages ?? []).some((item) => item.name === "result_check" && item.reason === "TIED_ROWS");
  // An ambiguous question answers with one reading; the others' results are a click away.
  const readings = run.assumption
    ? [
        { reading: run.assumption, sql: shownSql, columns: run.columns, rows: run.rows, truncated: run.truncated },
        ...(run.alternatives ?? []),
      ]
    : [];
  const [readingIndex, setReadingIndex] = useState(0);
  const shown = readings[readingIndex] ?? { sql: shownSql, columns: run.columns, rows: run.rows, truncated: run.truncated };
  // A column the model named as a percentage should lie between 0 and 100; say so when the shown rows do not.
  const outOfRange = executed ? percentOutOfRange(shown.columns, shown.rows) : [];

  const tabs: { id: Tab; label: string; hidden?: boolean }[] = [
    { id: "table", label: "Table", hidden: !executed },
    { id: "chart", label: "Chart", hidden: !executed },
    { id: "checks", label: "Checks" },
  ];

  return (
    <section className="result">
      <header className="result-head">
        <div className="result-title">
          <h1 className="result-question">{run.question}</h1>
          <span className={`pill pill-${outcome.tone}`}>{outcome.label}</span>
          {run.runtime_outcome === "EXECUTED" && addToReport}
        </div>
        <p className="result-meta">
          {run.runtime_outcome === "EXECUTED" && <span>{formatCount(run.row_count)} {run.row_count === 1 ? "row" : "rows"}</span>}
          <span>{formatMs(run.duration_ms)}</span>
          <span>{run.actor}</span>
          <span>{new Date(run.created_at).toLocaleString()}</span>
          <span>
            {run.proposal_source === "LIVE_MODEL"
              ? run.model ?? "Model"
              : run.proposal_source === "EDITED_QUERY"
                ? "Edited query, no model call"
                : "Replayed scenario"}
          </span>
        </p>
        {run.edited_from && (
          <p className="edited-note">
            The conditions were changed by hand; the question is the original wording, and the line below says what ran.
          </p>
        )}
      </header>

      <div className="query-disclosure">
        <button
          type="button"
          className="query-toggle"
          aria-expanded={queryOpen}
          aria-controls={`query-${run.run_id}`}
          disabled={!run.proposed_sql}
          onClick={() => setQueryOpen((open) => !open)}
        >
          {run.proposed_sql && <span className={queryOpen ? "chevron open" : "chevron"} aria-hidden="true" />}
          {run.proposed_sql ? (run.runtime_outcome === "EXECUTED" ? "Query that ran" : "Proposed query") : "No query was written"}
        </button>
        {queryOpen && shown.sql && (
          <div id={`query-${run.run_id}`}>
            <SqlViewer sql={shown.sql} />
          </div>
        )}
      </div>

      {executed && readings.length > 1 && (
        <div className="callout callout-neutral readings">
          <p>
            This question can be read more than one way, and the readings give different results. The answer below
            assumes one of them; choose another to see its result.
          </p>
          <div className="reading-options" role="radiogroup" aria-label="Readings of the question">
            {readings.map((item, index) => (
              <button
                type="button"
                role="radio"
                key={item.reading}
                aria-checked={readingIndex === index}
                className={readingIndex === index ? "reading-option active" : "reading-option"}
                onClick={() => setReadingIndex(index)}
              >
                {withCode(item.reading)}
              </button>
            ))}
          </div>
        </div>
      )}

      {changed && (
        <details className="repaired">
          <summary>The checks changed the model's query before it ran: {run.sql_changes.join(" ")}</summary>
          <SqlViewer sql={run.proposed_sql} />
        </details>
      )}

      {run.repaired_from_sql && (
        <details className="repaired">
          <summary>The checks sent the first query back, so the model rewrote it once.</summary>
          <SqlViewer sql={run.repaired_from_sql} />
        </details>
      )}

      {unmatched && run.runtime_outcome === "EXECUTED" && (
        <div className="callout callout-neutral">
          The query filters <code>{unmatched}</code> on a value the column does not store, so that filter matches no
          rows. If the result looks empty, the question may name a value that is spelled differently or is not in the data.
        </div>
      )}

      {tied && executed && (
        <div className="callout callout-neutral">
          The question asks for a highest or lowest value, and the query keeps every row that has it: these{" "}
          {formatCount(run.row_count)} rows share it rather than giving several different answers.
        </div>
      )}

      {outOfRange.length > 0 && (
        <div className="callout callout-neutral">
          <code>{outOfRange.join(", ")}</code> is a percentage outside 0 to 100. A share of a whole cannot be, so check
          the formula in the query before relying on it.
        </div>
      )}

      {run.explanation && run.runtime_outcome === "EXECUTED" && readingIndex === 0 && (
        <HowComputed explanation={run.explanation} onEdit={readOnly ? undefined : onEdit} busy={editing} />
      )}

      <GatePath
        trace={run.trace}
        onSelect={(selected) => {
          setStage(selected);
          setTab("checks");
        }}
      />

      {!run.results_retained && run.runtime_outcome === "EXECUTED" && !readOnly && (
        <div className="callout callout-neutral">
          <strong>Result rows are not kept in the audit log.</strong> The question, query and checks are shown as recorded.{" "}
          <button type="button" className="link" onClick={onRerun}>Run it again</button> to see current data.
        </div>
      )}

      {run.runtime_outcome !== "EXECUTED" && (
        <Explanation run={run} onAsk={readOnly ? undefined : onAsk} onManageConnections={onManageConnections} />
      )}

      <div className="tabs" role="tablist" aria-label="Result views" hidden={!executed}>
        {tabs.filter((item) => !item.hidden).map((item) => (
          <button
            type="button"
            role="tab"
            key={item.id}
            aria-selected={tab === item.id}
            className={tab === item.id ? "tab active" : "tab"}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="tab-panel" role="tabpanel">
        {tab === "table" && <ResultTable columns={shown.columns} rows={shown.rows} truncated={shown.truncated} />}
        {tab === "chart" && <ResultChart columns={shown.columns} rows={shown.rows} />}
        {tab === "checks" && <GovernancePanel run={run} stage={stage} onSelect={setStage} />}
      </div>
    </section>
  );
}

// The model writes column names in backticks; show them as code rather than as raw marks.
function withCode(text: string) {
  return text.split("`").map((part, index) => (index % 2 === 1 ? <code key={index}>{part}</code> : part));
}

function Explanation({
  run,
  onAsk,
  onManageConnections,
}: {
  run: RunRecord;
  onAsk?: (question: string) => void;
  onManageConnections: () => void;
}) {
  const decision = run.model_decision;
  const detail = run.model_detail;
  let title: string;
  let body: string;
  if (decision === "NEEDS_CLARIFICATION") {
    title = detail?.clarification_question ?? "This question can be read more than one way.";
    body = detail?.interpretations.length
      ? "Pick the reading you mean, or rephrase the question."
      : "Add the metric, time range or grouping you mean, then run it again.";
  } else if (decision === "BLOCKED_AUTHORITY") {
    title = "The data this question needs is outside your access.";
    body = detail?.missing_relationships.length
      ? "No authorized relationship connects the tables below. Declare how they join on the connection, then ask again."
      : "The model declined before writing any SQL. Ask an administrator to add the source if you need it.";
  } else if (decision === "BLOCKED_POLICY") {
    title = "This request is not allowed by the workspace policy.";
    body = "Only read-only questions can be answered here.";
  } else if (run.runtime_outcome === "AUTHORITY_REJECTED") {
    title = "The proposed query used a table outside this question's access.";
    body = "It was stopped before reaching the database. Open Checks to see which table.";
  } else if (run.runtime_outcome === "POLICY_REJECTED") {
    title = "The proposed query was not read-only.";
    body = "It was stopped before reaching the database. Only SELECT queries can run.";
  } else if (run.runtime_outcome === "EXECUTION_ERROR") {
    title = "The database could not run the query.";
    body = "Rephrase the question, or open the proposed query above to see what was attempted.";
  } else if (run.runtime_outcome === "GENERATION_ERROR") {
    title = "The model did not return a usable answer.";
    body = "Check that a model provider is configured, then try again.";
  } else {
    title = "The query was not run.";
    body = run.runtime_reason ? humanize(run.runtime_reason) : "Open Checks for details.";
  }
  const tone = runOutcome(run).tone === "danger" ? "danger" : "warning";
  return (
    <div className={`callout callout-${tone}`}>
      <strong>{title}</strong> {body}
      {run.model_reason_code && <code className="reason">{run.model_reason_code}</code>}
      {detail?.explanation && <span className="callout-detail">{detail.explanation}</span>}
      {detail && detail.missing_relationships.length > 0 && (
        <ul className="missing-joins">
          {detail.missing_relationships.map((item) => (
            <li key={`${item.from_table}-${item.to_table}`}>
              <code>{item.from_table}</code> and <code>{item.to_table}</code>
            </li>
          ))}
        </ul>
      )}
      {decision === "BLOCKED_AUTHORITY" && run.connection_id !== "governed" && (
        <button type="button" className="button ghost small callout-action" onClick={onManageConnections}>
          Open relationships
        </button>
      )}
      {decision === "NEEDS_CLARIFICATION" && onAsk && detail && detail.interpretations.length > 0 && (
        <ul className="interpretations">
          {detail.interpretations.map((reading) => (
            <li key={reading}>
              <button type="button" className="interpretation" onClick={() => onAsk(`${run.question}\n\nI mean: ${reading}`)}>
                {reading}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function percentOutOfRange(columns: string[], rows: Record<string, unknown>[]): string[] {
  return columns.filter(
    (name) =>
      /percent|pct/i.test(name) &&
      rows.some((row) => {
        const value = row[name];
        const number = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : NaN;
        return Number.isFinite(number) && (number < 0 || number > 100);
      }),
  );
}
