import { useState } from "react";
import { formatCount } from "../format";
import type { SchemaResponse } from "../types";

export function Sources({ schema }: { schema: SchemaResponse | null }) {
  const [selected, setSelected] = useState<string | null>(null);
  const entities = schema?.entities ?? [];
  const current = entities.find((entity) => entity.name === selected) ?? entities[0];
  const database = schema?.database;
  const referencedBy = current
    ? entities.filter((entity) => entity.relationships.some((relationship) => relationship.target_table === current.name))
    : [];

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Tables</h1>
          <p className="page-sub">
            {database
              ? `${database.name} on ${database.engine} ${database.server_version ?? ""}, schema ${database.schema_name}. ${entities.length} tables can be used in questions.`
              : "Loading the connected source…"}
          </p>
        </div>
      </header>

      <div className="sources">
        <ul className="source-list" aria-label="Tables">
          {entities.map((entity) => (
            <li key={entity.name}>
              <button
                type="button"
                className={current?.name === entity.name ? "source-row active" : "source-row"}
                onClick={() => setSelected(entity.name)}
              >
                <span>{entity.name}</span>
                <span className="tree-meta">{formatCount(entity.row_estimate)}</span>
              </button>
            </li>
          ))}
        </ul>

        {current && (
          <section className="source-detail" aria-label={`${current.name} table`}>
            <h2>{current.name}</h2>
            <p className="page-sub">{current.description}</p>
            <table className="data-table compact">
              <thead>
                <tr><th>Column</th><th>Type</th><th>Description</th></tr>
              </thead>
              <tbody>
                {current.columns.map((column) => {
                  const link = current.relationships.find((relationship) => relationship.source_column === column.name);
                  return (
                    <tr key={column.name}>
                      <td>
                        <code>{column.name}</code>
                        {column.primary_key && <span className="tag">key</span>}
                        {link && (
                          <button type="button" className="tag tag-link" onClick={() => setSelected(link.target_table)}>
                            {link.target_table}
                          </button>
                        )}
                      </td>
                      <td className="muted">{column.type}</td>
                      <td className="muted">{column.description}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {referencedBy.length > 0 && (
              <p className="page-sub">
                Referenced by{" "}
                {referencedBy.map((entity, index) => (
                  <span key={entity.name}>
                    {index > 0 && ", "}
                    <button type="button" className="link" onClick={() => setSelected(entity.name)}>{entity.name}</button>
                  </span>
                ))}
              </p>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
