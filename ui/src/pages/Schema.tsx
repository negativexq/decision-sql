import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SchemaResponse } from "../types";

export function Schema() {
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  useEffect(() => { void api.schema().then(setSchema); }, []);
  return <div className="page-stack"><section className="page-intro"><div><span className="eyebrow accent">CONTEXT</span><h2>Schema</h2><p>{schema?.title ?? "Loading model-visible context…"}</p></div><span className="count-pill">{schema?.entities.length ?? 0} relations</span></section><div className="schema-grid">{schema?.entities.map((entity) => <section className="card schema-card" key={entity.name}><div className="schema-heading"><div className="table-glyph">T</div><div><h3>{entity.name}</h3><p>{entity.description}</p></div></div><div className="column-list">{entity.columns.map((column) => <div className="column-row" key={column.name}><code>{column.name}</code><span>{column.type}</span>{column.primary_key && <b>PK</b>}</div>)}</div>{entity.relationships.length > 0 && <div className="relation-list"><span className="eyebrow">RELATIONSHIPS</span>{entity.relationships.map((relationship) => <div key={`${relationship.source_column}-${relationship.target_table}`}><code>{relationship.source_column}</code><span>→</span><code>{relationship.target_table}.{relationship.target_column}</code></div>)}</div>}</section>)}</div></div>;
}
