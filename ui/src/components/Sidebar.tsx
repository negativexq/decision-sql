import { type ReactNode, useState } from "react";
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
  library,
}: {
  view: View;
  onNavigate: (view: View) => void;
  onNewQuestion: () => void;
  schema: SchemaResponse | null;
  // The pinned, recent and report lists, already built by the app.
  library: ReactNode;
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

      {library}

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
  // The first run names the conversation; the newest one shows where it stands.
  root: string;
  last: RunSummary;
  question: string;
  turns: number;
}

/** Group runs into conversations: a follow-up or an edit continues the run it came from. */
export function conversations(all: RunSummary[]): Conversation[] {
  // A report reruns its cards' saved queries; those are not questions anyone asked here.
  const runs = all.filter((run) => run.proposal_source !== "SAVED_QUERY");
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
    // A chain whose start fell out of the recent window is named by its oldest run here.
    found.push({ root: root.follow_up_of ?? root.edited_from ?? root.run_id, last: run, question: root.question, turns });
  }
  return found;
}

export interface LibraryItem {
  kind: "conversation" | "report";
  id: string;
  title: string;
  pinned: boolean;
  meta?: string;
  // Reports a viewer cannot rename for everyone keep a name of the viewer's own.
  canRename: boolean;
}

/** Pinned items first, then recent conversations, then reports; each can be pinned and renamed. */
export function Library({
  pinned,
  recent,
  reports,
  current,
  onOpen,
  onPin,
  onRename,
  onAllReports,
}: {
  pinned: LibraryItem[];
  recent: LibraryItem[];
  reports: LibraryItem[];
  current: string | null;
  onOpen: (item: LibraryItem) => void;
  onPin: (item: LibraryItem, pinned: boolean) => void;
  onRename: (item: LibraryItem, title: string) => void;
  onAllReports: () => void;
}) {
  // Show a few of each; "Show more" opens the next ones, and the count stays per section.
  const [shown, setShown] = useState({ recent: RECENT_SHOWN, reports: REPORTS_SHOWN });
  const more = (key: keyof typeof shown, items: LibraryItem[], step: number) =>
    items.length > shown[key] ? (
      <button
        type="button"
        className="recent-more"
        onClick={() => setShown((current) => ({ ...current, [key]: current[key] + step }))}
      >
        <Icon path="M6 9l6 6 6-6" size={12} />
        Show more
      </button>
    ) : null;
  const section = (id: string, title: string, items: LibraryItem[], footer?: ReactNode) =>
    items.length > 0 || footer ? (
      <section className="recent" aria-labelledby={id}>
        <h2 id={id} className="recent-title">{title}</h2>
        <ul>
          {items.map((item) => (
            <LibraryRow
              key={`${item.kind}-${item.id}`}
              item={item}
              active={item.id === current}
              onOpen={() => onOpen(item)}
              onPin={() => onPin(item, !item.pinned)}
              onRename={(title) => onRename(item, title)}
            />
          ))}
        </ul>
        {footer}
      </section>
    ) : null;
  return (
    <>
      {section("pinned-title", "Pinned", pinned)}
      {section("recent-title", "Recent questions", recent.slice(0, shown.recent), more("recent", recent, 10))}
      {section(
        "reports-title",
        "Reports",
        reports.slice(0, shown.reports),
        more("reports", reports, 10) ?? (
          <button type="button" className="recent-more" onClick={onAllReports}>
            {reports.length ? "All reports" : "Create a report"}
          </button>
        ),
      )}
    </>
  );
}

const RECENT_SHOWN = 5;
const REPORTS_SHOWN = 3;
const PIN = "M12 17v5M8 3h8l-1 6 3 3v2H6v-2l3-3-1-6Z";
const PEN = "M4 20h4L19 9l-4-4L4 16v4ZM14 6l4 4";
const CHAT = "M4 5h16v11H9l-5 4V5Z";
const PAGE = "M4 4h7v9H4zM13 4h7v5h-7zM13 11h7v9h-7zM4 15h7v5H4z";

function LibraryRow({
  item,
  active,
  onOpen,
  onPin,
  onRename,
}: {
  item: LibraryItem;
  active: boolean;
  onOpen: () => void;
  onPin: () => void;
  onRename: (title: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.title);
  if (editing) {
    const finish = (save: boolean) => {
      // Enter closes the field, and its blur then fires too: save once.
      if (!editing) return;
      setEditing(false);
      if (save && draft.trim() !== item.title) onRename(draft.trim());
    };
    return (
      <li className="recent-row editing">
        <input
          className="recent-input"
          aria-label={`New name for ${item.title}`}
          value={draft}
          maxLength={200}
          autoFocus
          onFocus={(event) => event.currentTarget.select()}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => finish(true)}
          onKeyDown={(event) => {
            if (event.key === "Enter") finish(true);
            if (event.key === "Escape") finish(false);
          }}
        />
      </li>
    );
  }
  return (
    <li className={active ? "recent-row active" : "recent-row"}>
      <button
        type="button"
        className="recent-item"
        aria-current={active ? "true" : undefined}
        onClick={onOpen}
        title={item.title}
      >
        <Icon path={item.kind === "report" ? PAGE : CHAT} size={13} />
        <span className="recent-question">{item.title}</span>
        {item.meta && <span className="recent-turns">{item.meta}</span>}
      </button>
      <span className="recent-actions">
        {item.canRename && (
          <button
            type="button"
            className="recent-action"
            aria-label={`Rename ${item.title}`}
            title="Rename"
            onClick={() => {
              setDraft(item.title);
              setEditing(true);
            }}
          >
            <Icon path={PEN} size={13} />
          </button>
        )}
        <button
          type="button"
          className={item.pinned ? "recent-action pinned" : "recent-action"}
          aria-label={item.pinned ? `Unpin ${item.title}` : `Pin ${item.title}`}
          aria-pressed={item.pinned}
          title={item.pinned ? "Unpin" : "Pin to the top"}
          onClick={onPin}
        >
          <Icon path={PIN} size={13} />
        </button>
      </span>
    </li>
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
