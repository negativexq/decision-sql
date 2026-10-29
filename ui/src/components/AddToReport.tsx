import { useEffect, useRef, useState } from "react";
import { CHART_TYPES, suggestChart } from "./report/ReportChart";
import type { ChartType, Report, ReportSummary, RunRecord } from "../types";

/** Either an existing report, or a new one with this name. */
export type AddTarget = { reportId: string } | { title: string };

const NEW = "__new__";

/**
 * Pin an answer to a report as a chart card. Only reports on the answer's
 * connection that the user may edit are offered; the server pins the SQL that
 * ran from the audit log.
 */
export function AddToReport({
  run,
  reports,
  onAdd,
  onOpenReport,
}: {
  run: RunRecord;
  reports: ReportSummary[];
  onAdd: (target: AddTarget, chart: ChartType) => Promise<Report>;
  onOpenReport: (id: string) => void;
}) {
  const editable = reports.filter((report) => report.role !== "viewer" && report.connection_id === run.connection_id);
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState<string>(editable[0]?.id ?? NEW);
  const [title, setTitle] = useState(run.question.slice(0, 200));
  const [chart, setChart] = useState<ChartType>(() => (run.rows.length ? suggestChart(run.columns, run.rows) : "table"));
  const [state, setState] = useState<"idle" | "saving">("idle");
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<Report | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (added) {
    return (
      <span className="added" role="status">
        Added to <strong>{added.title}</strong>
        <button type="button" className="link" onClick={() => onOpenReport(added.id)}>
          Open report
        </button>
      </span>
    );
  }

  return (
    <div className="add-report" ref={box}>
      <button
        type="button"
        className="button ghost small"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          setError(null);
        }}
      >
        Add to report
      </button>
      {open && (
        <form
          className="add-report-panel"
          onKeyDown={(event) => {
            if (event.key === "Escape") setOpen(false);
          }}
          onSubmit={(event) => {
            event.preventDefault();
            if (target === NEW && !title.trim()) return;
            setState("saving");
            setError(null);
            onAdd(target === NEW ? { title: title.trim() } : { reportId: target }, chart)
              .then((report) => {
                setAdded(report);
                setOpen(false);
              })
              .catch((cause: Error) => setError(cause.message))
              .finally(() => setState("idle"));
          }}
        >
          <label className="field">
            <span className="field-label">Report</span>
            <select value={target} onChange={(event) => setTarget(event.target.value)} autoFocus>
              {editable.map((report) => (
                <option key={report.id} value={report.id}>
                  {report.title}
                </option>
              ))}
              <option value={NEW}>New report…</option>
            </select>
          </label>
          {target === NEW && (
            <label className="field">
              <span className="field-label">Name of the new report</span>
              <input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />
            </label>
          )}
          <label className="field">
            <span className="field-label">Show as</span>
            <select value={chart} onChange={(event) => setChart(event.target.value as ChartType)}>
              {CHART_TYPES.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          {editable.length === 0 && target === NEW && (
            <p className="panel-note">You have no report on this database yet, so this makes one.</p>
          )}
          {error && <p className="add-report-error" role="alert">{error}</p>}
          <div className="condition-actions">
            <button type="submit" className="button primary small" disabled={state === "saving" || (target === NEW && !title.trim())}>
              {state === "saving" ? "Adding" : "Add"}
            </button>
            <button type="button" className="button ghost small" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
