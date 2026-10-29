import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api/client";
import { relativeTime, runOutcome } from "../format";
import { ReportCanvas } from "../components/report/ReportCanvas";
import { CHART_TYPES, ReportChart, splitColumns, suggestChart } from "../components/report/ReportChart";
import { bottom } from "../components/report/layout";
import { AddFilterPanel, FilterBar, isEmpty } from "../components/report/ReportFilters";
import { SqlViewer } from "../components/SqlViewer";
import type {
  ChartType,
  ConnectionSummary,
  FilterChoice,
  Report,
  ReportAccess,
  ReportCard,
  ReportFilter,
  ReportSummary,
  RunRecord,
} from "../types";

type CardState =
  | { status: "loading" }
  // changed: tables described differently since the card was saved.
  // skipped: filters with a choice that this card's query does not read.
  | { status: "done"; run: RunRecord; changed: string[]; applied: string[]; skipped: string[] }
  | { status: "error"; message: string };

const DEFAULT_SIZE: Record<ChartType, { w: number; h: number }> = {
  number: { w: 3, h: 4 },
  bar: { w: 6, h: 8 },
  column: { w: 6, h: 8 },
  line: { w: 8, h: 7 },
  area: { w: 8, h: 7 },
  pie: { w: 4, h: 7 },
  table: { w: 6, h: 8 },
};

// A reader's filter choices are a per-viewer convenience: kept in this browser only.
function loadChoices(reportId: string): Record<string, FilterChoice> {
  try {
    return JSON.parse(localStorage.getItem(`decision-sql.report-filters.${reportId}`) ?? "{}") as Record<string, FilterChoice>;
  } catch {
    return {};
  }
}

function storeChoices(reportId: string, choices: Record<string, FilterChoice>) {
  try {
    localStorage.setItem(`decision-sql.report-filters.${reportId}`, JSON.stringify(choices));
  } catch {
    // Without storage the choices last until the page closes.
  }
}

function newId(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

/** Why a run produced no rows to chart, in the user's terms. */
function runProblem(run: RunRecord): string | null {
  if (run.runtime_outcome === "EXECUTED") return null;
  const detail = run.model_detail;
  if (detail?.clarification_question) return `The question needs one more detail: ${detail.clarification_question}`;
  if (detail?.explanation) return detail.explanation;
  return `${runOutcome(run).label}${run.runtime_reason ? ` (${run.runtime_reason})` : ""}.`;
}

function activeChoices(choices: Record<string, FilterChoice>): Record<string, FilterChoice> {
  return Object.fromEntries(Object.entries(choices).filter(([, choice]) => !isEmpty(choice)));
}

export function Reports() {
  const [openId, setOpenId] = useState<string | null>(null);
  return openId ? <ReportEditor reportId={openId} onClose={() => setOpenId(null)} /> : <ReportList onOpen={setOpenId} />;
}

function ReportList({ onOpen }: { onOpen: (id: string) => void }) {
  const [reports, setReports] = useState<ReportSummary[] | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [title, setTitle] = useState("");
  const [connectionId, setConnectionId] = useState("governed");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    void api.reports().then(setReports).catch((cause: Error) => setError(cause.message));
    void api.connections().then((items) => setConnections(items.filter((item) => item.queryable))).catch(() => setConnections([]));
  }, []);

  const names = Object.fromEntries(connections.map((item) => [item.id, item.name]));

  return (
    <div className="page reports-page">
      <header className="page-head">
        <div>
          <h1>Reports</h1>
          <p className="page-sub">Pages of charts and notes built from questions. Each chart keeps the query that answered it and runs it again, through the same checks, whenever the report opens.</p>
        </div>
      </header>

      <form
        className="report-new"
        onSubmit={(event) => {
          event.preventDefault();
          if (!title.trim()) return;
          setCreating(true);
          setError(null);
          void api
            .createReport(title.trim(), connectionId)
            .then((report) => onOpen(report.id))
            .catch((cause: Error) => setError(cause.message))
            .finally(() => setCreating(false));
        }}
      >
        <label className="field">
          <span className="field-label">Report name</span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Monthly sales review" maxLength={200} />
        </label>
        <label className="field">
          <span className="field-label">Database</span>
          <select value={connectionId} onChange={(event) => setConnectionId(event.target.value)}>
            {connections.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <button type="submit" className="button primary" disabled={!title.trim() || creating}>
          {creating ? "Creating" : "Create report"}
        </button>
      </form>

      {error && <div className="callout callout-danger">{error}</div>}
      {reports === null && !error && <p className="panel-note">Loading reports…</p>}
      {reports && reports.length === 0 && (
        <div className="empty-panel">
          <strong>No reports yet</strong>
          <p>Name one above, then add charts by asking questions.</p>
        </div>
      )}
      {reports && reports.length > 0 && (
        <ul className="report-list">
          {reports.map((report) => (
            <li key={report.id}>
              <button type="button" onClick={() => onOpen(report.id)}>
                <span className="report-list-title">{report.title}</span>
                <span className="report-list-meta">
                  {report.card_count} {report.card_count === 1 ? "card" : "cards"} on {names[report.connection_id] ?? report.connection_id}
                </span>
                <span className="report-list-meta">
                  {report.role === "owner" ? "Yours" : `Owned by ${report.owner}`}, {report.visibility === "workspace" ? "open to everyone" : "shared with named people"}
                  {report.role === "viewer" ? ", view only" : ""}
                </span>
                <span className="report-list-meta">Edited by {report.updated_by}, {relativeTime(report.updated_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ReportEditor({ reportId, onClose }: { reportId: string; onClose: () => void }) {
  const [report, setReport] = useState<Report | null>(null);
  const [cards, setCards] = useState<ReportCard[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [filters, setFilters] = useState<ReportFilter[]>([]);
  const [choices, setChoices] = useState<Record<string, FilterChoice>>(() => loadChoices(reportId));
  const choicesRef = useRef(choices);
  const [results, setResults] = useState<Record<string, CardState>>({});
  const [editing, setEditing] = useState(false);
  const [panel, setPanel] = useState<
    { kind: "add" } | { kind: "share" } | { kind: "filter" } | { kind: "card"; id: string } | null
  >(null);
  const [save, setSave] = useState<"saved" | "pending" | "saving" | "error">("saved");
  const [error, setError] = useState<string | null>(null);
  const [connectionName, setConnectionName] = useState("");
  const timer = useRef<number | null>(null);
  const latest = useRef({ title, description, cards, filters });
  latest.current = { title, description, cards, filters };

  const refresh = useCallback(
    (card: ReportCard) => {
      setResults((current) => ({ ...current, [card.id]: { status: "loading" } }));
      return api
        .refreshCard(reportId, card.id, activeChoices(choicesRef.current))
        .then(({ run, changed_tables, applied_filters, skipped_filters }) =>
          setResults((current) => ({
            ...current,
            [card.id]: { status: "done", run, changed: changed_tables, applied: applied_filters, skipped: skipped_filters },
          })),
        )
        .catch((cause: Error) => setResults((current) => ({ ...current, [card.id]: { status: "error", message: cause.message } })));
    },
    [reportId],
  );

  const accept = (card: ReportCard) =>
    api
      .acceptCard(reportId, card.id)
      .then((value) => {
        const basis = value.cards.find((item) => item.id === card.id)?.basis;
        setCards((current) => current.map((item) => (item.id === card.id ? { ...item, basis } : item)));
        setResults((current) => {
          const state = current[card.id];
          return state?.status === "done" ? { ...current, [card.id]: { ...state, changed: [] } } : current;
        });
      })
      .catch((cause: Error) => setError(cause.message));

  const refreshAll = useCallback(
    async (items: ReportCard[]) => {
      // A few at a time: every refresh is a real query on the source.
      const queue = items.filter((card) => card.kind === "query" && card.run_id);
      const workers = Array.from({ length: 3 }, async () => {
        for (let card = queue.shift(); card; card = queue.shift()) await refresh(card);
      });
      await Promise.all(workers);
    },
    [refresh],
  );

  useEffect(() => {
    void api
      .report(reportId)
      .then((value) => {
        setReport(value);
        setCards(value.cards);
        setTitle(value.title);
        setDescription(value.description);
        setFilters(value.filters);
        setEditing(value.cards.length === 0 && value.role !== "viewer");
        void refreshAll(value.cards);
        void api.connections().then((items) => setConnectionName(items.find((item) => item.id === value.connection_id)?.name ?? value.connection_id));
      })
      .catch((cause: Error) => setError(cause.message));
  }, [reportId, refreshAll]);

  const persist = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = null;
    setSave("saving");
    return api
      .saveReport(reportId, latest.current)
      .then((value) => {
        setReport(value);
        setSave("saved");
      })
      .catch((cause: Error) => {
        setSave("error");
        setError(cause.message);
      });
  }, [reportId]);

  const schedule = useCallback(() => {
    setSave("pending");
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void persist(), 700);
  }, [persist]);

  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current);
  }, []);

  const updateCards = (next: ReportCard[]) => {
    setCards(next);
    schedule();
  };
  const updateCard = (id: string, patch: Partial<ReportCard>) =>
    updateCards(cards.map((card) => (card.id === id ? { ...card, ...patch } : card)));
  const removeCard = (id: string) => {
    updateCards(cards.filter((card) => card.id !== id));
    setPanel(null);
  };

  const addQueryCard = (run: RunRecord, chart: ChartType) => {
    const size = DEFAULT_SIZE[chart];
    const card: ReportCard = {
      id: newId("card"),
      kind: "query",
      title: run.question,
      note: "",
      question: run.question,
      run_id: run.run_id,
      chart,
      dimension: null,
      measure: null,
      layout: { x: 0, y: bottom(cards), ...size },
    };
    setResults((current) => ({ ...current, [card.id]: { status: "done", run, changed: [], applied: [], skipped: [] } }));
    // The new card shows the answer to its question; refresh it if filters are set.
    if (Object.keys(activeChoices(choicesRef.current)).length) void refresh(card);
    updateCards([...cards, card]);
    setPanel(null);
  };

  const choose = (id: string, choice: FilterChoice) => {
    const next = { ...choicesRef.current, [id]: choice };
    choicesRef.current = next;
    setChoices(next);
    storeChoices(reportId, next);
    void refreshAll(cards);
  };

  const addFilter = (filter: ReportFilter) => {
    setFilters([...filters, filter]);
    schedule();
    setPanel(null);
  };

  const removeFilter = (id: string) => {
    setFilters(filters.filter((item) => item.id !== id));
    schedule();
    if (!isEmpty(choicesRef.current[id])) choose(id, {});
  };

  const addTextCard = () => {
    const card: ReportCard = {
      id: newId("text"),
      kind: "text",
      title: "Notes",
      note: "",
      question: "",
      run_id: null,
      chart: "table",
      dimension: null,
      measure: null,
      layout: { x: 0, y: bottom(cards), w: 4, h: 4 },
    };
    updateCards([...cards, card]);
    setPanel({ kind: "card", id: card.id });
  };

  if (error && !report) {
    return (
      <div className="page">
        <button type="button" className="button ghost small" onClick={onClose}>All reports</button>
        <div className="callout callout-danger">{error}</div>
      </div>
    );
  }
  if (!report) return <div className="page"><p className="panel-note">Opening report…</p></div>;

  const selected = panel?.kind === "card" ? cards.find((card) => card.id === panel.id) : undefined;
  const canEdit = report.role !== "viewer";
  const saveText = { saved: "Saved", pending: "Unsaved changes", saving: "Saving", error: "Not saved" }[save];

  return (
    <div className={panel ? "report-shell with-panel" : "report-shell"}>
      <div className="report-main">
        <div className="report-bar">
          <button type="button" className="button ghost small" onClick={() => void (save === "pending" ? persist() : Promise.resolve()).then(onClose)}>
            All reports
          </button>
          <span className={`report-save report-save-${save}`} role="status">{saveText}</span>
          <div className="report-actions">
            {editing && (
              <>
                <button type="button" className="button ghost small" onClick={() => setPanel({ kind: "add" })}>Add chart</button>
                <button type="button" className="button ghost small" onClick={addTextCard}>Add text</button>
              </>
            )}
            {!editing && (
              <button type="button" className="button ghost small" onClick={() => void refreshAll(cards)}>Refresh data</button>
            )}
            {report.role === "owner" && (
              <button type="button" className="button ghost small" onClick={() => setPanel({ kind: "share" })}>Share</button>
            )}
            {canEdit && <button
              type="button"
              className={editing ? "button primary small" : "button ghost small"}
              onClick={() => {
                if (editing && save === "pending") void persist();
                setEditing(!editing);
                if (editing) setPanel(null);
              }}
            >
              {editing ? "Done editing" : "Edit report"}
            </button>}
          </div>
        </div>

        <header className="report-head">
          {editing ? (
            <>
              <input
                className="report-title-input"
                aria-label="Report name"
                value={title}
                maxLength={200}
                onChange={(event) => {
                  setTitle(event.target.value);
                  schedule();
                }}
              />
              <textarea
                className="report-description-input"
                aria-label="Report description"
                placeholder="What this report is for, who reads it, and how to read its numbers."
                value={description}
                maxLength={4000}
                rows={2}
                onChange={(event) => {
                  setDescription(event.target.value);
                  schedule();
                }}
              />
            </>
          ) : (
            <>
              <h1 className="report-title">{title}</h1>
              {description && <p className="report-description">{description}</p>}
            </>
          )}
          <p className="report-meta">
            {connectionName} · {report.role === "owner" ? "yours" : `owned by ${report.created_by}`}
            {report.role === "viewer" ? ", view only" : ""} · edited by {report.updated_by}, {relativeTime(report.updated_at)}
          </p>
        </header>

        <FilterBar
          reportId={reportId}
          filters={filters}
          choices={choices}
          editing={editing}
          onChoose={choose}
          onRemove={removeFilter}
          onAdd={() => setPanel({ kind: "filter" })}
        />

        {error && report && <div className="callout callout-danger">{error}</div>}

        {cards.length === 0 ? (
          <div className="report-empty">
            <strong>Start with a question</strong>
            {canEdit ? (
              <>
                <p>Ask something like “Revenue by month this year”. The answer becomes a chart you can resize, move, and describe.</p>
                <button type="button" className="button primary" onClick={() => { setEditing(true); setPanel({ kind: "add" }); }}>Add chart</button>
              </>
            ) : (
              <p>The owner has not added charts yet.</p>
            )}
          </div>
        ) : (
          <ReportCanvas
            cards={cards}
            editing={editing}
            onChange={updateCards}
            renderCard={(card, handle) => (
              <CardView
                card={card}
                state={results[card.id]}
                editing={editing}
                handle={handle}
                selected={selected?.id === card.id}
                onSelect={() => setPanel({ kind: "card", id: card.id })}
                onRefresh={() => void refresh(card)}
                onAccept={canEdit ? () => void accept(card) : undefined}
                filterLabels={Object.fromEntries(filters.map((item) => [item.id, item.label]))}
                onChart={(chart) => updateCard(card.id, { chart })}
              />
            )}
          />
        )}
      </div>

      {panel?.kind === "filter" && (
        <AddFilterPanel reportId={reportId} existing={filters} onAdd={addFilter} onClose={() => setPanel(null)} />
      )}
      {panel?.kind === "share" && (
        <SharePanel
          report={report}
          connectionName={connectionName}
          onShared={(value) => setReport(value)}
          onClose={() => setPanel(null)}
        />
      )}
      {panel?.kind === "add" && (
        <AddChartPanel reportId={reportId} onAdd={addQueryCard} onClose={() => setPanel(null)} />
      )}
      {selected && (
        <CardPanel
          card={selected}
          state={results[selected.id]}
          onChange={(patch) => updateCard(selected.id, patch)}
          onRemove={() => removeCard(selected.id)}
          onClose={() => setPanel(null)}
        />
      )}
    </div>
  );
}

function CardView({
  card,
  state,
  editing,
  handle,
  selected,
  onSelect,
  onRefresh,
  onAccept,
  filterLabels,
  onChart,
}: {
  card: ReportCard;
  state: CardState | undefined;
  editing: boolean;
  handle: ReactNode;
  selected: boolean;
  onSelect: () => void;
  onRefresh: () => void;
  onAccept?: () => void;
  filterLabels: Record<string, string>;
  onChart: (chart: ChartType) => void;
}) {
  const changed = state?.status === "done" ? state.changed : [];
  const skipped = state?.status === "done" ? state.skipped.map((id) => filterLabels[id] ?? id) : [];
  const className = ["report-card", card.kind === "text" ? "report-card-text" : "", selected ? "selected" : ""].filter(Boolean).join(" ");
  return (
    <article className={className}>
      <header className="report-card-head">
        {handle}
        <h3 className="report-card-title" title={card.question || card.title}>{card.title || card.question || "Untitled"}</h3>
        {editing && card.kind === "query" && (
          <label className="report-card-type">
            <span className="visually-hidden">Chart type</span>
            <select value={card.chart} onChange={(event) => onChart(event.target.value as ChartType)}>
              {CHART_TYPES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </label>
        )}
        {editing && (
          <button type="button" className="button ghost small" onClick={onSelect}>Edit</button>
        )}
      </header>
      {changed.length > 0 && (
        <div className="report-card-drift" role="status">
          <p>
            {changed.length === 1 ? "Table" : "Tables"} {changed.join(", ")} changed since this card was saved. The query passed
            every check again; make sure the chart still shows what its title says.
          </p>
          {onAccept && <button type="button" className="link" onClick={onAccept}>Mark as checked</button>}
        </div>
      )}
      <div className="report-card-body">
        {card.kind === "text" ? (
          card.note ? <p className="report-card-prose">{card.note}</p> : <p className="rc-empty">Write notes for this section in Edit.</p>
        ) : (
          <CardBody card={card} state={state} onRefresh={onRefresh} />
        )}
      </div>
      {skipped.length > 0 && (
        <p className="report-card-unfiltered">Not filtered by {skipped.join(", ")}: this chart's query does not read that table.</p>
      )}
      {card.kind === "query" && card.note && <p className="report-card-note">{card.note}</p>}
    </article>
  );
}

function CardBody({ card, state, onRefresh }: { card: ReportCard; state: CardState | undefined; onRefresh: () => void }) {
  if (!card.run_id) return <p className="rc-empty">This card has no query.</p>;
  if (!state || state.status === "loading") return <div className="rc-loading" aria-label="Running the saved query" />;
  if (state.status === "error") {
    return (
      <div className="rc-empty">
        {state.message} <button type="button" className="link" onClick={onRefresh}>Try again</button>
      </div>
    );
  }
  const problem = runProblem(state.run);
  if (problem) {
    return (
      <div className="rc-empty">
        The saved query did not run: {problem} <button type="button" className="link" onClick={onRefresh}>Try again</button>
      </div>
    );
  }
  return (
    <ReportChart
      type={card.chart}
      columns={state.run.columns}
      rows={state.run.rows}
      dimension={card.dimension}
      measure={card.measure}
      emptyText={state.applied.length ? "No rows match the current filters." : undefined}
    />
  );
}

function AddChartPanel({
  reportId,
  onAdd,
  onClose,
}: {
  reportId: string;
  onAdd: (run: RunRecord, chart: ChartType) => void;
  onClose: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [run, setRun] = useState<RunRecord | null>(null);
  const [chart, setChart] = useState<ChartType>("table");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const problem = run ? runProblem(run) : null;

  const ask = (text = question) => {
    if (!text.trim()) return;
    setBusy(true);
    setError(null);
    setRun(null);
    void api
      .askForCard(reportId, text.trim())
      .then((value) => {
        setRun(value);
        setChart(suggestChart(value.columns, value.rows));
      })
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setBusy(false));
  };

  return (
    <aside className="report-panel" aria-label="Add chart">
      <div className="report-panel-head">
        <h2>Add chart</h2>
        <button type="button" className="button ghost small" onClick={onClose}>Close</button>
      </div>
      <form
        className="report-panel-body"
        onSubmit={(event) => {
          event.preventDefault();
          ask();
        }}
      >
        <label className="field">
          <span className="field-label">Question</span>
          <textarea
            className="report-question"
            value={question}
            rows={3}
            maxLength={1000}
            placeholder="Revenue by month this year"
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) ask();
            }}
          />
        </label>
        <button type="submit" className="button primary" disabled={!question.trim() || busy}>
          {busy ? "Answering" : "Ask"}
        </button>
      </form>

      {error && <div className="callout callout-danger">{error}</div>}
      {run && problem && (
        <div className="callout callout-warning">
          <strong>No chart from this question.</strong> {problem}
          {(run.model_detail?.interpretations.length ?? 0) > 0 && (
            <ul className="report-interpretations">
              {run.model_detail?.interpretations.map((item) => (
                <li key={item}>
                  <button
                    type="button"
                    className="link"
                    disabled={busy}
                    onClick={() => {
                      setQuestion(item);
                      ask(item);
                    }}
                  >
                    {item}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {run && !problem && (
        <div className="report-panel-body">
          <fieldset className="chart-picker">
            <legend className="field-label">Show as</legend>
            <div className="chart-picker-options">
              {CHART_TYPES.map((item) => (
                <label key={item.id} className={chart === item.id ? "chart-option active" : "chart-option"}>
                  <input type="radio" name="chart" value={item.id} checked={chart === item.id} onChange={() => setChart(item.id)} />
                  <ChartGlyph type={item.id} />
                  {item.label}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="report-preview">
            <ReportChart type={chart} columns={run.columns} rows={run.rows} dimension={null} measure={null} />
          </div>
          {run.executed_sql && (
            <details className="report-sql">
              <summary>Query that will be saved</summary>
              <SqlViewer sql={run.executed_sql} />
            </details>
          )}
          <button type="button" className="button primary" onClick={() => onAdd(run, chart)}>Add to report</button>
        </div>
      )}
    </aside>
  );
}

function people(text: string): string[] {
  return text.split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean);
}

function SharePanel({
  report,
  connectionName,
  onShared,
  onClose,
}: {
  report: Report;
  connectionName: string;
  onShared: (report: Report) => void;
  onClose: () => void;
}) {
  const [visibility, setVisibility] = useState(report.visibility);
  const [editors, setEditors] = useState(report.editors.join(", "));
  const [viewers, setViewers] = useState(report.viewers.join(", "));
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const access: ReportAccess = { visibility, editors: people(editors), viewers: people(viewers) };
    setState("saving");
    setError(null);
    void api
      .shareReport(report.id, access)
      .then((value) => {
        onShared(value);
        setEditors(value.editors.join(", "));
        setViewers(value.viewers.join(", "));
        setState("saved");
      })
      .catch((cause: Error) => {
        setError(cause.message);
        setState("idle");
      });
  };

  return (
    <aside className="report-panel" aria-label="Share report">
      <div className="report-panel-head">
        <h2>Share</h2>
        <button type="button" className="button ghost small" onClick={onClose}>Close</button>
      </div>
      <form
        className="report-panel-body"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <fieldset className="share-visibility">
          <legend className="field-label">Who can open it</legend>
          <label>
            <input type="radio" name="visibility" checked={visibility === "private"} onChange={() => setVisibility("private")} />
            <span><strong>Named people</strong> You, the editors, and the viewers below.</span>
          </label>
          <label>
            <input type="radio" name="visibility" checked={visibility === "workspace"} onChange={() => setVisibility("workspace")} />
            <span><strong>Everyone</strong> Anyone using this workspace can open and refresh it.</span>
          </label>
        </fieldset>
        <label className="field">
          <span className="field-label">Editors<span className="field-hint">change charts and text</span></span>
          <textarea rows={2} value={editors} placeholder="ayse.kaya@example.com, mehmet" onChange={(event) => { setEditors(event.target.value); setState("idle"); }} />
        </label>
        <label className="field">
          <span className="field-label">Viewers<span className="field-hint">open and refresh</span></span>
          <textarea rows={2} value={viewers} placeholder="Names, separated by commas" onChange={(event) => { setViewers(event.target.value); setState("idle"); }} />
        </label>
        <p className="panel-note">
          Everyone who opens this report sees what the database user of {connectionName || "this connection"} can read; sharing a report shares
          that reach. Names are the ones people set in this app, not verified sign-ins.
        </p>
        {error && <div className="callout callout-danger">{error}</div>}
        <button type="submit" className="button primary" disabled={state === "saving"}>
          {state === "saving" ? "Saving" : state === "saved" ? "Saved" : "Save sharing"}
        </button>
      </form>
    </aside>
  );
}

function CardPanel({
  card,
  state,
  onChange,
  onRemove,
  onClose,
}: {
  card: ReportCard;
  state: CardState | undefined;
  onChange: (patch: Partial<ReportCard>) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const run = state?.status === "done" ? state.run : null;
  const { numeric, other } = run ? splitColumns(run.columns, run.rows) : { numeric: [], other: [] };
  return (
    <aside className="report-panel" aria-label="Edit card">
      <div className="report-panel-head">
        <h2>{card.kind === "text" ? "Text" : "Chart"}</h2>
        <button type="button" className="button ghost small" onClick={onClose}>Close</button>
      </div>
      <div className="report-panel-body">
        <label className="field">
          <span className="field-label">Title</span>
          <input value={card.title} maxLength={200} onChange={(event) => onChange({ title: event.target.value })} />
        </label>
        <label className="field">
          <span className="field-label">{card.kind === "text" ? "Text" : "Description"}<span className="field-hint">shown {card.kind === "text" ? "in the card" : "under the chart"}</span></span>
          <textarea
            className="report-note-input"
            value={card.note}
            rows={card.kind === "text" ? 8 : 4}
            maxLength={4000}
            placeholder={card.kind === "text" ? "Context, definitions, or conclusions for this part of the report." : "What to notice in this chart, or how its numbers are defined."}
            onChange={(event) => onChange({ note: event.target.value })}
          />
        </label>
        {card.kind === "query" && (
          <>
            <fieldset className="chart-picker">
              <legend className="field-label">Show as</legend>
              <div className="chart-picker-options">
                {CHART_TYPES.map((item) => (
                  <label key={item.id} className={card.chart === item.id ? "chart-option active" : "chart-option"}>
                    <input type="radio" name={`chart-${card.id}`} value={item.id} checked={card.chart === item.id} onChange={() => onChange({ chart: item.id })} />
                    <ChartGlyph type={item.id} />
                    {item.label}
                  </label>
                ))}
              </div>
            </fieldset>
            {run && (
              <div className="report-axes">
                <label className="field">
                  <span className="field-label">Category</span>
                  <select value={card.dimension ?? ""} onChange={(event) => onChange({ dimension: event.target.value || null })}>
                    <option value="">Automatic</option>
                    {[...other, ...numeric].map((column) => <option key={column} value={column}>{column}</option>)}
                  </select>
                </label>
                <label className="field">
                  <span className="field-label">Value</span>
                  <select value={card.measure ?? ""} onChange={(event) => onChange({ measure: event.target.value || null })}>
                    <option value="">Automatic</option>
                    {numeric.map((column) => <option key={column} value={column}>{column}</option>)}
                  </select>
                </label>
              </div>
            )}
            <div className="report-question-shown">
              <span className="field-label">Question</span>
              <p>{card.question}</p>
            </div>
            <dl className="report-provenance">
              <dt>Saved from run</dt>
              <dd><code>{card.run_id}</code></dd>
              {run && (
                <>
                  <dt>Last run</dt>
                  <dd><code>{run.run_id}</code>, {relativeTime(run.created_at)}</dd>
                </>
              )}
              {state?.status === "done" && state.changed.length > 0 && (
                <>
                  <dt>Changed since saved</dt>
                  <dd>{state.changed.join(", ")}</dd>
                </>
              )}
            </dl>
            {run?.executed_sql && (
              <details className="report-sql">
                <summary>Saved query</summary>
                <SqlViewer sql={run.executed_sql} />
              </details>
            )}
          </>
        )}
        <button type="button" className="button danger" onClick={onRemove}>Remove card</button>
      </div>
    </aside>
  );
}

/** Small drawings of each chart type, so the choice reads at a glance. */
function ChartGlyph({ type }: { type: ChartType }) {
  const paths: Record<ChartType, ReactNode> = {
    number: <text x="12" y="16" textAnchor="middle" fontSize="11" fontWeight="600" fill="currentColor">42</text>,
    bar: <path d="M4 6h12M4 12h8M4 18h14" strokeWidth="3" />,
    column: <path d="M6 20V10M12 20V5M18 20v-7" strokeWidth="3" />,
    line: <path d="M3 17l5-6 4 3 8-9" strokeWidth="1.8" />,
    area: <path d="M3 19V15l5-5 4 3 9-8v14z" fill="currentColor" opacity=".35" strokeWidth="1.4" />,
    pie: <path d="M12 3a9 9 0 1 0 9 9h-9z M14 2.5V10h7.5A8 8 0 0 0 14 2.5z" fill="currentColor" opacity=".55" strokeWidth="0" />,
    table: <path d="M3 5h18v14H3zM3 10h18M3 15h18M10 5v14" strokeWidth="1.4" />,
  };
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true" className="chart-glyph" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
      {paths[type]}
    </svg>
  );
}
