import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { relativeTime } from "../format";
import type { ConnectionRelationships, IntrospectedTable, RelationshipDeclaration } from "../types";

const EMPTY: RelationshipDeclaration = { from_table: "", from_column: "", to_table: "", to_column: "" };

function label(item: RelationshipDeclaration) {
  return (
    <>
      <code>{item.from_table}.{item.from_column}</code>
      <span className="rel-arrow" aria-label="points to">→</span>
      <code>{item.to_table}.{item.to_column}</code>
    </>
  );
}

/** Joins questions may use on this source: its foreign keys plus the ones declared here. */
export function Relationships({ connectionId, tables }: { connectionId: string; tables: IntrospectedTable[] | null }) {
  const [data, setData] = useState<ConnectionRelationships | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState<RelationshipDeclaration>(EMPTY);

  const load = useCallback(() => {
    void api
      .relationships(connectionId)
      .then((value) => {
        setData(value);
        setError(null);
      })
      .catch((cause: Error) => setError(cause.message));
  }, [connectionId]);

  useEffect(() => {
    setData(null);
    setDraft(EMPTY);
    load();
  }, [load]);

  const declare = (declaration: RelationshipDeclaration, key: string) => {
    setBusy(key);
    setError(null);
    void api
      .declareRelationship(connectionId, declaration)
      .then(() => {
        setDraft(EMPTY);
        load();
      })
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setBusy(null));
  };

  const columnsOf = (table: string) => tables?.find((item) => item.name === table)?.columns ?? [];
  const complete = Object.values(draft).every(Boolean);

  return (
    <section className="relationships" aria-labelledby={`rel-${connectionId}`}>
      <h3 className="panel-title" id={`rel-${connectionId}`}>Relationships</h3>
      <p className="panel-note">
        Questions can join two tables only through a relationship listed here. Foreign keys come from the database;
        declare the others yourself. Each declaration is checked: the target column must hold unique values.
      </p>

      {error && <div className="callout callout-danger">{error}</div>}
      {data === null && !error && <p className="panel-note">Reading relationships…</p>}

      {data && (
        <>
          <h4 className="panel-subtitle">Declared here {data.declared.length > 0 && <span className="muted">{data.declared.length}</span>}</h4>
          {data.declared.length === 0 ? (
            <p className="panel-note">None yet.</p>
          ) : (
            <ul className="rel-list">
              {data.declared.map((item) => (
                <li key={item.id}>
                  <span className="rel-name">{label(item)}</span>
                  <span className="rel-meta">{item.created_by}, {relativeTime(item.created_at)}</span>
                  <button
                    type="button"
                    className="button ghost small"
                    disabled={busy !== null}
                    onClick={() => {
                      setBusy(item.id);
                      void api
                        .removeRelationship(connectionId, item.id)
                        .then(load)
                        .catch((cause: Error) => setError(cause.message))
                        .finally(() => setBusy(null));
                    }}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}

          {data.suggestions.length > 0 && (
            <>
              <h4 className="panel-subtitle">Suggested <span className="muted">{data.suggestions.length}</span></h4>
              <ul className="rel-list">
                {data.suggestions.map((item) => {
                  const key = `${item.from_table}.${item.from_column}>${item.to_table}.${item.to_column}`;
                  return (
                    <li key={key}>
                      <span className="rel-name">{label(item)}</span>
                      <span className="rel-meta">
                        {item.reason}
                        {item.match_rate !== null && ` ${Math.round(item.match_rate * 100)}% of sampled values match.`}
                      </span>
                      <button
                        type="button"
                        className="button ghost small"
                        disabled={busy !== null}
                        onClick={() =>
                          declare(
                            {
                              from_table: item.from_table,
                              from_column: item.from_column,
                              to_table: item.to_table,
                              to_column: item.to_column,
                            },
                            key,
                          )
                        }
                      >
                        {busy === key ? "Checking" : "Declare"}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}

          <h4 className="panel-subtitle">Declare another</h4>
          <form
            className="rel-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (complete) declare(draft, "manual");
            }}
          >
            <TableColumn
              legend="Many rows in"
              tables={tables}
              table={draft.from_table}
              column={draft.from_column}
              columns={columnsOf(draft.from_table)}
              onChange={(table, column) => setDraft({ ...draft, from_table: table, from_column: column })}
            />
            <TableColumn
              legend="point to one row in"
              tables={tables}
              table={draft.to_table}
              column={draft.to_column}
              columns={columnsOf(draft.to_table)}
              onChange={(table, column) => setDraft({ ...draft, to_table: table, to_column: column })}
            />
            <button type="submit" className="button primary" disabled={!complete || busy !== null}>
              {busy === "manual" ? "Checking" : "Declare relationship"}
            </button>
          </form>

          <h4 className="panel-subtitle">From the database <span className="muted">{data.foreign_keys.length}</span></h4>
          {data.foreign_keys.length === 0 ? (
            <p className="panel-note">This source declares no foreign keys.</p>
          ) : (
            <ul className="rel-list">
              {data.foreign_keys.map((item) => (
                <li key={`${item.from_table}.${item.from_column}>${item.to_table}.${item.to_column}`}>
                  <span className="rel-name">{label(item)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function TableColumn({
  legend,
  tables,
  table,
  column,
  columns,
  onChange,
}: {
  legend: string;
  tables: IntrospectedTable[] | null;
  table: string;
  column: string;
  columns: { name: string; type: string }[];
  onChange: (table: string, column: string) => void;
}) {
  return (
    <fieldset className="rel-pick">
      <legend>{legend}</legend>
      <select aria-label={`${legend}: table`} value={table} onChange={(event) => onChange(event.target.value, "")}>
        <option value="">Table</option>
        {tables?.map((item) => (
          <option key={item.name} value={item.name}>{item.name}</option>
        ))}
      </select>
      <select
        aria-label={`${legend}: column`}
        value={column}
        disabled={!table}
        onChange={(event) => onChange(table, event.target.value)}
      >
        <option value="">Column</option>
        {columns.map((item) => (
          <option key={item.name} value={item.name}>{item.name} ({item.type})</option>
        ))}
      </select>
    </fieldset>
  );
}
