import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { relativeTime } from "../format";
import type { ConnectionValues, ValueColumn } from "../types";

const PREVIEW = 6;

function name(column: ValueColumn) {
  return `${column.table}.${column.column}`;
}

function preview(column: ValueColumn) {
  const shown = column.values.slice(0, PREVIEW).join(", ");
  const more = column.values.length - PREVIEW;
  return more > 0 ? `${shown} and ${more} more` : shown;
}

/** Whether questions on this source may show the model how its categorical columns are spelled. */
export function ValueSharing({ connectionId }: { connectionId: string }) {
  const [data, setData] = useState<ConnectionValues | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const apply = (value: ConnectionValues) => {
    setData(value);
    setEnabled(value.enabled);
    setExcluded(new Set(value.excluded));
  };

  const load = useCallback(() => {
    void api
      .values(connectionId)
      .then((value) => {
        apply(value);
        setError(null);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [connectionId]);

  useEffect(() => {
    setData(null);
    load();
  }, [load]);

  const changed =
    data !== null &&
    (enabled !== data.enabled ||
      excluded.size !== data.excluded.length ||
      data.excluded.some((item) => !excluded.has(item)));

  const save = () => {
    setSaving(true);
    setError(null);
    void api
      .setValues(connectionId, enabled, [...excluded].sort())
      .then(apply)
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setSaving(false));
  };

  const toggle = (key: string) =>
    setExcluded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <section className="value-sharing" aria-labelledby={`values-${connectionId}`}>
      <h3 className="panel-title" id={`values-${connectionId}`}>Stored values</h3>
      <p className="panel-note">
        A question about “premium” stations finds nothing when the column stores “Premium”. Sharing lets the model see
        how short text columns are spelled, read from database statistics. These are real values and are sent to the
        model provider with each question, so they are off until you turn them on. Columns whose names suggest personal
        data are never read. While sharing is off, the model sees only the shape of code and date columns, such as
        9999-99-99.
      </p>

      {error && <div className="callout callout-danger">{error}</div>}
      {data === null && !error && <p className="panel-note">Reading statistics…</p>}

      {data && !data.available && (
        <p className="panel-note">Value hints are turned off for every connection on this server (DECISION_SQL_VALUE_EXAMPLES).</p>
      )}

      {data && data.available && (
        <>
          <label className="value-toggle">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
            Share stored values with the model
          </label>

          {data.columns.length === 0 ? (
            <p className="panel-note">No column has values to share. Run ANALYZE on the source if its tables are new.</p>
          ) : (
            <ul className="rel-list value-list">
              {data.columns.map((column) => {
                const key = name(column);
                return (
                  <li key={key}>
                    <input
                      type="checkbox"
                      aria-label={`Share ${key}`}
                      checked={!excluded.has(key)}
                      disabled={!enabled}
                      onChange={() => toggle(key)}
                    />
                    <code className="rel-name">{key}</code>
                    <span className="rel-meta">
                      {preview(column)}
                      {column.complete ? " (all values)" : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="value-actions">
            <button type="button" className="button primary" disabled={!changed || saving} onClick={save}>
              {saving ? "Saving" : "Save"}
            </button>
            {data.updated_by && data.updated_at && (
              <span className="rel-meta">
                Last changed by {data.updated_by}, {relativeTime(data.updated_at)}
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}
