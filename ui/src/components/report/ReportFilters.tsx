import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { FilterChoice, FilterColumn, FilterKind, ReportFilter } from "../../types";

export function isEmpty(choice: FilterChoice | undefined): boolean {
  return !choice || (!(choice.values?.length) && !choice.start && !choice.end);
}

function humanize(column: string): string {
  const text = column.replace(/_/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The filters a reader narrows the report by, above the cards. */
export function FilterBar({
  reportId,
  filters,
  choices,
  editing,
  onChoose,
  onRemove,
  onAdd,
}: {
  reportId: string;
  filters: ReportFilter[];
  choices: Record<string, FilterChoice>;
  editing: boolean;
  onChoose: (id: string, choice: FilterChoice) => void;
  onRemove: (id: string) => void;
  onAdd: () => void;
}) {
  if (filters.length === 0 && !editing) return null;
  const active = filters.filter((item) => !isEmpty(choices[item.id]));
  return (
    <div className="filter-bar" role="group" aria-label="Report filters">
      {filters.map((item) => (
        <div key={item.id} className={isEmpty(choices[item.id]) ? "filter-chip" : "filter-chip active"}>
          {item.kind === "values" ? (
            <ValuesFilter reportId={reportId} filter={item} choice={choices[item.id]} onChoose={(choice) => onChoose(item.id, choice)} />
          ) : (
            <DateFilter filter={item} choice={choices[item.id]} onChoose={(choice) => onChoose(item.id, choice)} />
          )}
          {editing && (
            <button type="button" className="filter-remove" aria-label={`Remove the ${item.label} filter`} onClick={() => onRemove(item.id)}>
              ×
            </button>
          )}
        </div>
      ))}
      {editing && (
        <button type="button" className="button ghost small" onClick={onAdd}>Add filter</button>
      )}
      {active.length > 0 && !editing && (
        <button type="button" className="link filter-clear" onClick={() => active.forEach((item) => onChoose(item.id, {}))}>
          Clear filters
        </button>
      )}
    </div>
  );
}

function ValuesFilter({
  reportId,
  filter,
  choice,
  onChoose,
}: {
  reportId: string;
  filter: ReportFilter;
  choice: FilterChoice | undefined;
  onChoose: (choice: FilterChoice) => void;
}) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<{ values: string[]; truncated: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<string[]>(choice?.values ?? []);
  const ref = useRef<HTMLDivElement | null>(null);
  const chosen = choice?.values ?? [];

  useEffect(() => {
    if (!open) return;
    setPicked(choice?.values ?? []);
    if (options === null) {
      // Reading the values is a gated query of its own, so it runs once, on first open.
      api
        .filterOptions(reportId, filter.id)
        .then((value) => setOptions(value))
        .catch((cause: Error) => setError(cause.message));
    }
    const close = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = (options?.values ?? []).filter((value) => value.toLowerCase().includes(search.toLowerCase()));
  const summary = chosen.length === 0 ? "All" : chosen.length === 1 ? chosen[0] : `${chosen.length} selected`;

  return (
    <div className="filter-values" ref={ref}>
      <button type="button" className="filter-trigger" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="filter-label">{filter.label}</span>
        <span className="filter-summary">{summary}</span>
      </button>
      {open && (
        <div
          className="filter-popover"
          role="dialog"
          aria-label={`${filter.label} values`}
          onKeyDown={(event) => {
            if (event.key === "Escape") setOpen(false);
          }}
        >
          {error && <p className="filter-error">{error}</p>}
          {!error && options === null && <p className="filter-note">Reading values…</p>}
          {options && (
            <>
              {options.values.length > 8 && (
                <input
                  className="filter-search"
                  placeholder="Search"
                  aria-label={`Search ${filter.label}`}
                  value={search}
                  autoFocus
                  onChange={(event) => setSearch(event.target.value)}
                />
              )}
              <ul className="filter-options">
                {visible.map((value) => (
                  <li key={value}>
                    <label>
                      <input
                        type="checkbox"
                        checked={picked.includes(value)}
                        onChange={(event) =>
                          setPicked(event.target.checked ? [...picked, value] : picked.filter((item) => item !== value))
                        }
                      />
                      <span>{value}</span>
                    </label>
                  </li>
                ))}
                {visible.length === 0 && <li className="filter-note">No values match.</li>}
              </ul>
              {options.truncated && <p className="filter-note">Showing the first {options.values.length} values.</p>}
            </>
          )}
          <div className="filter-actions">
            <button type="button" className="button ghost small" onClick={() => setPicked([])}>Clear</button>
            <button
              type="button"
              className="button primary small"
              onClick={() => {
                onChoose(picked.length ? { values: picked } : {});
                setOpen(false);
              }}
            >
              Apply
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function DateFilter({
  filter,
  choice,
  onChoose,
}: {
  filter: ReportFilter;
  choice: FilterChoice | undefined;
  onChoose: (choice: FilterChoice) => void;
}) {
  const start = choice?.start ?? "";
  const end = choice?.end ?? "";
  const set = (next: { start: string; end: string }) =>
    onChoose(next.start || next.end ? { start: next.start || null, end: next.end || null } : {});
  return (
    <div className="filter-dates">
      <span className="filter-label">{filter.label}</span>
      <input type="date" aria-label={`${filter.label} from`} value={start} max={end || undefined} onChange={(event) => set({ start: event.target.value, end })} />
      <span aria-hidden="true">to</span>
      <input type="date" aria-label={`${filter.label} to`} value={end} min={start || undefined} onChange={(event) => set({ start, end: event.target.value })} />
    </div>
  );
}

export function AddFilterPanel({
  reportId,
  existing,
  onAdd,
  onClose,
}: {
  reportId: string;
  existing: ReportFilter[];
  onAdd: (filter: ReportFilter) => void;
  onClose: () => void;
}) {
  const [columns, setColumns] = useState<FilterColumn[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState<FilterKind>("values");

  useEffect(() => {
    api.filterColumns(reportId).then(setColumns).catch((cause: Error) => setError(cause.message));
  }, [reportId]);

  const tables = useMemo(() => {
    const groups = new Map<string, FilterColumn[]>();
    for (const column of columns ?? []) groups.set(column.table, [...(groups.get(column.table) ?? []), column]);
    return [...groups.entries()];
  }, [columns]);
  const selected = columns?.find((column) => `${column.table}.${column.column}` === key);

  return (
    <aside className="report-panel" aria-label="Add filter">
      <div className="report-panel-head">
        <h2>Add filter</h2>
        <button type="button" className="button ghost small" onClick={onClose}>Close</button>
      </div>
      <form
        className="report-panel-body"
        onSubmit={(event) => {
          event.preventDefault();
          if (!selected) return;
          onAdd({
            id: `f_${Math.random().toString(36).slice(2, 10)}`,
            label: label.trim() || humanize(selected.column),
            table: selected.table,
            column: selected.column,
            kind,
          });
        }}
      >
        <p className="panel-note">
          A filter narrows every chart whose query reads its table. Charts that do not read it stay as they are and say so.
        </p>
        {error && <div className="callout callout-danger">{error}</div>}
        <label className="field">
          <span className="field-label">Column</span>
          <select
            value={key}
            disabled={!columns}
            onChange={(event) => {
              setKey(event.target.value);
              const column = columns?.find((item) => `${item.table}.${item.column}` === event.target.value);
              setKind(column?.kinds.includes("date_range") ? "date_range" : "values");
              setLabel("");
            }}
          >
            <option value="">{columns ? "Choose a column" : "Loading columns…"}</option>
            {tables.map(([table, items]) => (
              <optgroup key={table} label={table}>
                {items.map((column) => {
                  const value = `${column.table}.${column.column}`;
                  const taken = existing.some((item) => item.table === column.table && item.column === column.column);
                  return (
                    <option key={value} value={value} disabled={taken}>
                      {column.column} ({column.type})
                    </option>
                  );
                })}
              </optgroup>
            ))}
          </select>
        </label>
        {selected && (
          <>
            <label className="field">
              <span className="field-label">Label</span>
              <input value={label} maxLength={80} placeholder={humanize(selected.column)} onChange={(event) => setLabel(event.target.value)} />
            </label>
            {selected.kinds.length > 1 && (
              <fieldset className="share-visibility">
                <legend className="field-label">Pick by</legend>
                <label>
                  <input type="radio" name="filter-kind" checked={kind === "date_range"} onChange={() => setKind("date_range")} />
                  <span><strong>Date range</strong> From one day to another.</span>
                </label>
                <label>
                  <input type="radio" name="filter-kind" checked={kind === "values"} onChange={() => setKind("values")} />
                  <span><strong>Values</strong> One or more of the values the column holds.</span>
                </label>
              </fieldset>
            )}
          </>
        )}
        <button type="submit" className="button primary" disabled={!selected}>Add filter</button>
      </form>
    </aside>
  );
}
