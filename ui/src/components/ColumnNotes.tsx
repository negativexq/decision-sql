import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { relativeTime } from "../format";
import type { ColumnNote, IntrospectedTable } from "../types";

const key = (table: string, column: string) => `${table}.${column}`;

/** Descriptions of columns whose names do not say what they hold, shown to the model in place of the source's comments. */
export function ColumnNotes({ connectionId, tables }: { connectionId: string; tables: IntrospectedTable[] | null }) {
  const [saved, setSaved] = useState<Map<string, ColumnNote>>(new Map());
  const [drafts, setDrafts] = useState<Map<string, string>>(new Map());
  const [table, setTable] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const apply = (notes: ColumnNote[]) => {
    setSaved(new Map(notes.map((note) => [key(note.table, note.column), note])));
    setDrafts(new Map());
  };

  const load = useCallback(() => {
    void api
      .notes(connectionId)
      .then((value) => {
        apply(value.notes);
        setError(null);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [connectionId]);

  useEffect(() => {
    setTable("");
    load();
  }, [load]);

  const chosen = useMemo(() => tables?.find((item) => item.name === table) ?? null, [tables, table]);
  const noted = useMemo(() => new Set([...saved.values()].map((note) => note.table)), [saved]);
  const changes = [...drafts.entries()].filter(([name, text]) => text.trim() !== (saved.get(name)?.description ?? ""));

  const save = () => {
    setSaving(true);
    setError(null);
    const notes = changes.map(([name, description]) => {
      const dot = name.indexOf(".");
      return { table: name.slice(0, dot), column: name.slice(dot + 1), description };
    });
    void api
      .setNotes(connectionId, notes)
      .then((value) => apply(value.notes))
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setSaving(false));
  };

  const latest = [...saved.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];

  return (
    <section className="value-sharing" aria-labelledby={`notes-${connectionId}`}>
      <h3 className="panel-title" id={`notes-${connectionId}`}>
        Column descriptions {saved.size > 0 && <span className="muted">{saved.size}</span>}
      </h3>
      <p className="panel-note">
        When a column’s name does not say what it holds, such as <code>a4</code> for a district’s population, the model
        asks instead of guessing. Describe it here and every question on this source sees the description in place of the
        database comment. A description explains a column; it never lets a query reach more data.
      </p>

      {error && <div className="callout callout-danger">{error}</div>}

      <label className="notes-table">
        Table{" "}
        <select value={table} onChange={(event) => setTable(event.target.value)} disabled={!tables}>
          <option value="">Choose a table</option>
          {(tables ?? []).map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
              {noted.has(item.name) ? " (described)" : ""}
            </option>
          ))}
        </select>
      </label>

      {chosen && (
        <ul className="rel-list notes-list">
          {chosen.columns.map((column) => {
            const name = key(chosen.name, column.name);
            const value = drafts.get(name) ?? saved.get(name)?.description ?? "";
            return (
              <li key={name}>
                <code className="rel-name">{column.name}</code>
                <span className="rel-meta">{column.type}</span>
                <input
                  type="text"
                  aria-label={`Description of ${name}`}
                  maxLength={500}
                  placeholder="What this column holds"
                  value={value}
                  onChange={(event) => setDrafts((current) => new Map(current).set(name, event.target.value))}
                />
              </li>
            );
          })}
        </ul>
      )}

      <div className="value-actions">
        <button type="button" className="button primary" disabled={changes.length === 0 || saving} onClick={save}>
          {saving ? "Saving" : changes.length > 1 ? `Save ${changes.length} descriptions` : "Save"}
        </button>
        {latest && (
          <span className="rel-meta">
            Last changed by {latest.updated_by}, {relativeTime(latest.updated_at)}
          </span>
        )}
      </div>
    </section>
  );
}
