import { type ReactNode, useEffect, useRef, useState } from "react";
import type { Condition, ConditionEdit, SqlExplanation } from "../types";

const VERBS: Record<SqlExplanation["measures"][number]["function"], string> = {
  count: "Counts",
  sum: "Adds up",
  average: "Averages",
  min: "Takes the lowest",
  max: "Takes the highest",
};

/**
 * One line on how the answer was computed, read from the query itself. Each
 * condition is a chip: change its values or drop it, and the query runs again
 * through every check without asking the model.
 */
export function HowComputed({
  explanation,
  onEdit,
  busy,
}: {
  explanation: SqlExplanation;
  onEdit?: (edits: ConditionEdit[]) => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState<number | null>(null);
  const { tables, measures, conditions, groups, order, limit, distinct } = explanation;
  return (
    <div className="how" aria-label="How the answer was computed">
      <span className="how-label">How it was computed</span>
      <p className="how-line">
        Reads {list(tables.map((name) => <code key={name}>{name}</code>))}.
        {measures.length > 0 && (
          <>
            {" "}
            {list(
              measures.map((measure, index) => (
                <span key={index}>
                  {index === 0 ? VERBS[measure.function] : VERBS[measure.function].toLowerCase()}{" "}
                  {measure.column ? (
                    <>
                      {measure.distinct && "distinct "}
                      <code>{measure.column}</code>
                    </>
                  ) : (
                    "rows"
                  )}
                </span>
              )),
            )}
            .
          </>
        )}
        {distinct && measures.length === 0 && " Keeps distinct rows."}
        {conditions.length > 0 && (
          <>
            {" "}Keeps rows where{" "}
            {conditions.map((condition) => (
              <ConditionChip
                key={condition.index}
                condition={condition}
                open={open === condition.index}
                editable={Boolean(onEdit) && !busy}
                onToggle={() => setOpen(open === condition.index ? null : condition.index)}
                onApply={(edit) => {
                  setOpen(null);
                  onEdit?.([edit]);
                }}
              />
            ))}
            .
          </>
        )}
        {groups.length > 0 && <> One row per {list(groups.map((name) => <code key={name}>{name}</code>))}.</>}
        {order.length > 0 && (
          <>
            {" "}Sorted by{" "}
            {list(
              order.map((item) => (
                <span key={item.column}>
                  <code>{item.column}</code> {item.descending ? "highest first" : "lowest first"}
                </span>
              )),
            )}
            {limit !== null ? `, first ${limit}` : ""}.
          </>
        )}
        {order.length === 0 && limit !== null && <> First {limit} rows.</>}
      </p>
      {busy && <p className="how-busy" role="status">Running the changed query through the checks…</p>}
    </div>
  );
}

function ConditionChip({
  condition,
  open,
  editable,
  onToggle,
  onApply,
}: {
  condition: Condition;
  open: boolean;
  editable: boolean;
  onToggle: () => void;
  onApply: (edit: ConditionEdit) => void;
}) {
  const between = condition.operator === "between";
  const many = condition.operator.endsWith("in");
  const [draft, setDraft] = useState(condition.values);
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) {
      setDraft(condition.values);
      first.current?.focus();
    }
  }, [open, condition.values]);
  const shown = between ? `${condition.values[0]} and ${condition.values[1]}` : condition.values.join(", ");
  const values = many ? draft.join(",").split(",").map((value) => value.trim()).filter(Boolean) : draft.map((value) => value.trim());
  // A date stored with a time, or as text, is edited as text so it keeps its form.
  const dayOnly = condition.kind === "date" && condition.values.every((value) => /^\d{4}-\d{2}-\d{2}$/.test(value));
  const changed = values.join("\u0000") !== condition.values.join("\u0000");
  const valid = values.length > 0 && values.every(Boolean) && (!between || values.length === 2);
  return (
    <span className="condition">
      <button
        type="button"
        className={open ? "condition-chip open" : "condition-chip"}
        aria-expanded={open}
        disabled={!editable}
        onClick={onToggle}
        title={editable ? "Change this condition" : undefined}
      >
        <code>{condition.column}</code> {condition.operator} <strong>{shown}</strong>
      </button>
      {open && (
        <form
          className="condition-editor"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid && changed) onApply({ index: condition.index, values });
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") onToggle();
          }}
        >
          <label className="condition-field">
            <span>
              <code>{condition.column}</code> {condition.operator}
              {many && " (separate values with commas)"}
            </span>
            <input
              ref={first}
              type={dayOnly && !many ? "date" : "text"}
              inputMode={condition.kind === "number" ? "decimal" : undefined}
              value={many ? draft.join(", ") : draft[0] ?? ""}
              onChange={(event) => setDraft(many ? [event.target.value] : [event.target.value, ...draft.slice(1)])}
            />
          </label>
          {between && (
            <label className="condition-field">
              <span>and</span>
              <input
                type={dayOnly ? "date" : "text"}
                inputMode={condition.kind === "number" ? "decimal" : undefined}
                value={draft[1] ?? ""}
                onChange={(event) => setDraft([draft[0] ?? "", event.target.value])}
              />
            </label>
          )}
          <div className="condition-actions">
            <button type="submit" className="button primary small" disabled={!valid || !changed}>
              Run with this
            </button>
            {condition.removable && (
              <button type="button" className="button ghost small" onClick={() => onApply({ index: condition.index, remove: true })}>
                Remove condition
              </button>
            )}
            <button type="button" className="button ghost small" onClick={onToggle}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </span>
  );
}

function list(items: ReactNode[]) {
  return items.map((item, index) => (
    <span key={index}>
      {index > 0 && (index === items.length - 1 ? " and " : ", ")}
      {item}
    </span>
  ));
}
