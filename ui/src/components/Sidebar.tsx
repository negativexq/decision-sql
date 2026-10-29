import { useState } from "react";
import { formatCount } from "../format";
import type { RunSummary, SchemaResponse } from "../types";

export type View = "ask" | "reports" | "audit" | "sources" | "connections";

const nav: { id: View; label: string; icon: string }[] = [
  { id: "ask", label: "Ask", icon: "M4 6h16M4 12h10M4 18h7" },
  { id: "reports", label: "Reports", icon: "M4 4h7v9H4zM13 4h7v5h-7zM13 11h7v9h-7zM4 15h7v5H4z" },
  { id: "audit", label: "Audit log", icon: "M12 7v5l3 2M4 12a8 8 0 1 0 2.3-5.6M4 4v3h3" },
  { id: "sources", label: "Tables", icon: "M4 5h16v14H4zM4 10h16M10 10v9" },
  { id: "connections", label: "Connections", icon: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" },
];

export function Icon({ path, size = 16 }: { path: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="icon">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Sidebar({
  view,
  onNavigate,
  onNewQuestion,
  schema,
  actor,
  onActorChange,
  recent,
  current,
  onOpenRecent,
}: {
  view: View;
  onNavigate: (view: View) => void;
  onNewQuestion: () => void;
  schema: SchemaResponse | null;
  recent: RunSummary[];
  current: string | null;
  onOpenRecent: (run: RunSummary) => void;
  actor: string;
  onActorChange: (actor: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const database = schema?.database;
  return (
    <aside className="sidebar" aria-label="Workspace">
      <div className="workspace">
        <span className="workspace-mark" aria-hidden="true">D</span>
        <span className="workspace-name">Decision-SQL</span>
      </div>
      <button type="button" className="new-question" onClick={onNewQuestion}>
        <Icon path="M12 5v14M5 12h14" />
        New question
        <kbd>N</kbd>
      </button>
      <nav className="nav" aria-label="Primary">
        {nav.map((item) => (
          <button
            type="button"
            key={item.id}
            className={view === item.id ? "nav-item active" : "nav-item"}
            aria-current={view === item.id ? "page" : undefined}
            onClick={() => onNavigate(item.id)}
          >
            <Icon path={item.icon} />
            {item.label}
          </button>
        ))}
      </nav>

      <Recent runs={recent} current={current} onOpen={onOpenRecent} />

      <div className="source">
        <div className="source-head">
          <span>Connected source</span>
        </div>
        <div className="source-db">
          <span className={database?.connected ? "dot dot-success" : "dot dot-neutral"} aria-hidden="true" />
          <div>
            <strong>{database?.name ?? "Loading…"}</strong>
            <small>
              {database ? `${database.engine} ${database.server_version ?? ""}`.trim() : ""}
              {database && !database.connected && " (unreachable)"}
            </small>
          </div>
        </div>
        <ul className="table-tree" aria-label="Tables available to questions">
          {schema?.entities.map((entity) => {
            const expanded = open === entity.name;
            return (
              <li key={entity.name}>
                <button
                  type="button"
                  className="tree-row"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : entity.name)}
                  title={entity.description}
                >
                  <span className={expanded ? "chevron open" : "chevron"} aria-hidden="true" />
                  <Icon path="M4 5h16v14H4zM4 10h16M10 10v9" size={14} />
                  <span className="tree-name">{entity.name}</span>
                  <span className="tree-meta">{formatCount(entity.row_estimate)}</span>
                </button>
                {expanded && (
                  <ul className="column-tree">
                    {entity.columns.map((column) => (
                      <li key={column.name}>
                        <span className="tree-name">{column.name}</span>
                        <span className="tree-meta">{column.primary_key ? "key" : column.type}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      <Identity actor={actor} onChange={onActorChange} />
    </aside>
  );
}

interface Conversation {
  // The newest run, which opens the whole thread.
  last: RunSummary;
  title: string;
  turns: number;
}

/** Group runs into conversations: a follow-up or an edit continues the run it came from. */
export function conversations(runs: RunSummary[], limit = 8): Conversation[] {
  const byId = new Map(runs.map((run) => [run.run_id, run]));
  const continued = new Set(runs.flatMap((run) => [run.follow_up_of, run.edited_from]).filter(Boolean) as string[]);
  const found: Conversation[] = [];
  for (const run of runs) {
    if (continued.has(run.run_id)) continue;
    let root = run;
    let turns = 1;
    for (let parent = byId.get(root.follow_up_of ?? root.edited_from ?? ""); parent && turns < 50; turns += 1) {
      root = parent;
      parent = byId.get(root.follow_up_of ?? root.edited_from ?? "");
    }
    found.push({ last: run, title: root.question, turns });
    if (found.length >= limit) break;
  }
  return found;
}

function Recent({ runs, current, onOpen }: { runs: RunSummary[]; current: string | null; onOpen: (run: RunSummary) => void }) {
  const items = conversations(runs);
  if (items.length === 0) return null;
  return (
    <section className="recent" aria-labelledby="recent-title">
      <h2 id="recent-title" className="recent-title">Recent questions</h2>
      <ul>
        {items.map((item) => (
          <li key={item.last.run_id}>
            <button
              type="button"
              className={item.last.run_id === current ? "recent-item active" : "recent-item"}
              aria-current={item.last.run_id === current ? "true" : undefined}
              onClick={() => onOpen(item.last)}
              title={item.title}
            >
              <span className="recent-question">{item.title}</span>
              {item.turns > 1 && <span className="recent-turns">{item.turns}</span>}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Self-declared name recorded in the audit log; replaced by SSO identity when available. */
function Identity({ actor, onChange }: { actor: string; onChange: (actor: string) => void }) {
  const [editing, setEditing] = useState(!actor);
  const [draft, setDraft] = useState(actor);
  if (editing) {
    return (
      <form
        className="identity"
        onSubmit={(event) => {
          event.preventDefault();
          onChange(draft.trim());
          setEditing(!draft.trim());
        }}
      >
        <label className="identity-label" htmlFor="actor">Your work email</label>
        <input
          id="actor"
          type="email"
          value={draft}
          placeholder="name@company.com"
          onChange={(event) => setDraft(event.target.value)}
        />
        <small>Recorded with each question in the audit log.</small>
      </form>
    );
  }
  return (
    <button type="button" className="identity identity-set" onClick={() => setEditing(true)} title="Change name">
      <span className="avatar" aria-hidden="true">{actor.charAt(0).toUpperCase()}</span>
      <span className="identity-name">{actor}</span>
    </button>
  );
}
