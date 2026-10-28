import { useEffect, useRef, useState } from "react";
import { KIND_LABEL } from "../format";
import type { ConnectionSummary } from "../types";

/** Composer control for choosing which connected database a question runs against. */
export function SourcePicker({
  connections,
  selected,
  onSelect,
  onManage,
}: {
  connections: ConnectionSummary[];
  selected: string;
  onSelect: (id: string) => void;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const current = connections.find((item) => item.id === selected);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    root.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const moveFocus = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = Array.from(root.current?.querySelectorAll<HTMLButtonElement>(".source-option") ?? []);
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.key === "ArrowDown" ? index + 1 : index - 1;
    items[(next + items.length) % items.length]?.focus();
  };

  return (
    <div className="source-picker" ref={root}>
      <button
        type="button"
        className="source-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="visually-hidden">Database: </span>
        <DbIcon />
        <span className="source-trigger-name">{current?.name ?? "Governed source"}</span>
        <span className="source-trigger-caret" aria-hidden="true" />
      </button>
      {open && (
        <div className="source-menu" role="menu" aria-label="Choose a database" onKeyDown={moveFocus}>
          {connections.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitemradio"
              aria-checked={item.id === selected}
              className="source-option"
              onClick={() => {
                onSelect(item.id);
                setOpen(false);
              }}
            >
              <span className="source-option-main">
                <span className="source-option-name">{item.name}</span>
                <span className="source-option-meta">
                  {KIND_LABEL[item.kind] ?? item.kind} · {item.database}
                  {item.schema_name ? `.${item.schema_name}` : ""}
                </span>
              </span>
              <span className={`source-option-state ${item.queryable ? "is-ready" : ""}`}>
                {item.queryable ? "Questions" : "Browse only"}
              </span>
              {item.id === selected && <span className="source-check" aria-hidden="true" />}
            </button>
          ))}
          <div className="source-menu-foot">
            <button
              type="button"
              className="source-option source-manage"
              onClick={() => {
                setOpen(false);
                onManage();
              }}
            >
              Add or manage connections
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function DbIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <ellipse cx="8" cy="3.5" rx="5.5" ry="2" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2.5 3.5v9c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2v-9M2.5 8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}
