import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type ConnectionInput } from "../api/client";
import { ColumnNotes } from "../components/ColumnNotes";
import { Relationships } from "../components/Relationships";
import { ValueSharing } from "../components/ValueSharing";
import { KIND_LABEL, formatCount, formatMs, relativeTime } from "../format";
import type { ConnectionSummary, ConnectionTestResult, ConnectorType, DriverInstallResult, IntrospectedTable } from "../types";

export function Connections() {
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [connectors, setConnectors] = useState<ConnectorType[]>([]);
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = () =>
    api.connections().then(setConnections).catch((cause: Error) => setError(cause.message));

  useEffect(() => {
    void reload();
    void api.connectors().then(setConnectors).catch(() => setConnectors([]));
  }, []);

  const current = connections.find((item) => item.id === selected) ?? null;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Connections</h1>
          <p className="page-sub">Databases this workspace can read from. Credentials are encrypted and never shown again.</p>
        </div>
        {!adding && (
          <button type="button" className="button primary" onClick={() => setAdding(true)}>
            Add connection
          </button>
        )}
      </header>

      {error && <div className="callout callout-danger" role="alert">{error}</div>}

      {adding && (
        <AddConnection
          connectors={connectors}
          onCancel={() => setAdding(false)}
          onDriverInstalled={() => void api.connectors().then(setConnectors)}
          onSaved={(connection) => {
            setAdding(false);
            setSelected(connection.id);
            void reload();
          }}
        />
      )}

      <ul className="list conn-list">
        {connections.map((connection) => (
          <li key={connection.id}>
            <button
              type="button"
              className={selected === connection.id ? "list-row conn-row active" : "list-row conn-row"}
              onClick={() => setSelected(selected === connection.id ? null : connection.id)}
              aria-expanded={selected === connection.id}
            >
              <span className={`dot dot-${statusTone(connection)}`} aria-hidden="true" />
              <span className="list-main">
                {connection.name}
                {connection.governed && <span className="tag">Used for questions</span>}
              </span>
              <span className="list-outcome">{KIND_LABEL[connection.kind]}</span>
              <span className="conn-address">{connection.host}{connection.port ? `:${connection.port}` : ""}/{connection.database}</span>
              <span className="list-time">{connection.built_in ? "System" : connection.created_by}</span>
            </button>
            {selected === connection.id && (
              <ConnectionDetail
                connection={connection}
                onChanged={() => void reload()}
                onDeleted={() => {
                  setSelected(null);
                  void reload();
                }}
              />
            )}
          </li>
        ))}
      </ul>
      {current === null && connections.length > 1 && (
        <p className="page-sub">Select a connection to test it or browse its tables.</p>
      )}
    </div>
  );
}

function statusTone(connection: ConnectionSummary): string {
  if (connection.built_in) return "success";
  if (connection.last_test_ok === true) return "success";
  if (connection.last_test_ok === false) return "danger";
  return "neutral";
}

function ConnectionDetail({
  connection,
  onChanged,
  onDeleted,
}: {
  connection: ConnectionSummary;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [tables, setTables] = useState<IntrospectedTable[] | null>(null);
  const [busy, setBusy] = useState<"test" | "delete" | null>(null);
  const [result, setResult] = useState<ConnectionTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (connection.built_in) {
      void api.schema().then((schema) =>
        setTables(
          schema.entities.map((entity) => ({
            name: entity.name,
            row_estimate: entity.row_estimate,
            columns: entity.columns.map((column) => ({ name: column.name, type: column.type, nullable: true })),
          })),
        ),
      );
      return;
    }
    void api.connectionTables(connection.id).then(setTables).catch((cause: Error) => setError(cause.message));
  }, [connection.id, connection.built_in]);

  return (
    <div className="conn-detail">
      <dl className="facts">
        <div><dt>Type</dt><dd>{KIND_LABEL[connection.kind]}</dd></div>
        <div><dt>Host</dt><dd>{connection.host}{connection.port ? `:${connection.port}` : ""}</dd></div>
        <div><dt>Database</dt><dd>{connection.database}</dd></div>
        <div><dt>Schema</dt><dd>{connection.schema_name}</dd></div>
        <div><dt>User</dt><dd>{connection.username}</dd></div>
        <div>
          <dt>Last check</dt>
          <dd>
            {connection.built_in
              ? "Checked on every question"
              : connection.last_tested_at
                ? `${connection.last_test_ok ? "Passed" : "Failed"} ${relativeTime(connection.last_tested_at)}`
                : "Never"}
          </dd>
        </div>
      </dl>

      {connection.built_in ? (
        <p className="panel-note">
          This is the governed source questions run against. It is configured on the server and cannot be edited here.
        </p>
      ) : (
        <p className="panel-note">
          {connection.queryable
            ? "Questions can run on this connection. They read its schema each time and pass the same policy, access and cost checks."
            : "This connection can be tested and its tables browsed. Questions run on PostgreSQL connections for now."}
        </p>
      )}

      {(result || error || connection.last_test_message) && (
        <div className={`callout ${result?.ok === false || error ? "callout-danger" : "callout-neutral"}`}>
          {error ?? result?.message ?? connection.last_test_message}
        </div>
      )}

      {!connection.built_in && (
        <div className="row-actions">
          <button
            type="button"
            className="button ghost"
            disabled={busy !== null}
            onClick={() => {
              setBusy("test");
              setError(null);
              void api
                .retestConnection(connection.id)
                .then((value) => {
                  setResult(value);
                  onChanged();
                })
                .catch((cause: Error) => setError(cause.message))
                .finally(() => setBusy(null));
            }}
          >
            {busy === "test" ? "Testing" : "Test connection"}
          </button>
          {confirming ? (
            <>
              <span className="confirm-text">Remove {connection.name}? Past audit records are kept.</span>
              <button
                type="button"
                className="button danger"
                disabled={busy !== null}
                onClick={() => {
                  setBusy("delete");
                  void api
                    .deleteConnection(connection.id)
                    .then(onDeleted)
                    .catch((cause: Error) => setError(cause.message))
                    .finally(() => setBusy(null));
                }}
              >
                Remove connection
              </button>
              <button type="button" className="button ghost" onClick={() => setConfirming(false)}>Keep</button>
            </>
          ) : (
            <button type="button" className="button ghost" onClick={() => setConfirming(true)}>Remove</button>
          )}
        </div>
      )}

      {!connection.built_in && connection.queryable && <Relationships connectionId={connection.id} tables={tables} />}
      {!connection.built_in && connection.queryable && <ValueSharing connectionId={connection.id} />}
      {!connection.built_in && connection.queryable && <ColumnNotes connectionId={connection.id} tables={tables} />}

      <h3 className="panel-title">Tables {tables && <span className="muted">{tables.length}</span>}</h3>
      {tables === null && !error && <p className="panel-note">Reading the schema…</p>}
      {tables && (
        <ul className="scope-list">
          {tables.map((table) => (
            <li key={table.name}>
              <strong>
                {table.name} <span className="muted">{formatCount(table.row_estimate)} rows</span>
              </strong>
              <span>{table.columns.map((column) => column.name).join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const CORE_FIELDS = new Set(["host", "port", "database", "username", "password", "schema_name", "ssl_mode"]);

function AddConnection({
  connectors,
  onCancel,
  onSaved,
  onDriverInstalled,
}: {
  connectors: ConnectorType[];
  onCancel: () => void;
  onSaved: (connection: ConnectionSummary) => void;
  onDriverInstalled: () => void;
}) {
  const [kind, setKind] = useState<string | null>(null);
  const connector = connectors.find((item) => item.kind === kind && item.available) ?? null;
  const initial = useMemo(() => defaults(connector), [connector]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"test" | "save" | null>(null);
  const [preflight, setPreflight] = useState<{ result: ConnectionTestResult; signature: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setValues(initial);
    setPreflight(null);
    setError(null);
  }, [initial]);

  const payload = (): ConnectionInput => {
    const body: ConnectionInput = { kind: kind ?? "" };
    const options: Record<string, string> = {};
    for (const [key, value] of Object.entries(values)) {
      if (value === "") continue;
      if (key === "name") body.name = value;
      else if (CORE_FIELDS.has(key)) body[key] = key === "port" ? Number(value) : value;
      else options[key] = value;
    }
    if (Object.keys(options).length) body.options = options;
    return body;
  };

  if (!connector) {
    return <ConnectorPicker connectors={connectors} onPick={setKind} onCancel={onCancel} onDriverInstalled={onDriverInstalled} />;
  }

  const signature = JSON.stringify(values);
  const current = preflight && preflight.signature === signature ? preflight.result : null;
  const stale = preflight !== null && preflight.signature !== signature;
  const missing = connector.fields.some((field) => field.required && !values[field.name]) || !values.name;
  const badField = current && !current.ok ? current.field : null;

  const runPreflight = () => {
    setBusy("test");
    setError(null);
    void api
      .testConnection(payload())
      .then((result) => setPreflight({ result, signature }))
      .catch((cause: Error) => setError(cause.message))
      .finally(() => setBusy(null));
  };

  return (
    <section className="panel" aria-label={`New ${connector.label} connection`}>
      <div className="panel-head">
        <h2 className="panel-heading">New {connector.label} connection</h2>
        <button type="button" className="button ghost small" onClick={() => setKind(null)}>Change type</button>
      </div>
      <form
        className="conn-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!current?.ok) return;
          setBusy("save");
          setError(null);
          void api
            .createConnection(payload())
            .then((response) => {
              if (response.saved) onSaved(response.connection);
              else setPreflight({ result: response.test, signature });
            })
            .catch((cause: Error) => setError(cause.message))
            .finally(() => setBusy(null));
        }}
      >
        <Field label="Name" hint="Shown to everyone in this workspace">
          <input
            required
            value={values.name ?? ""}
            onChange={(event) => setValues({ ...values, name: event.target.value })}
            placeholder="Sales warehouse"
          />
        </Field>
        {connector.fields.map((field) => (
          <Field
            key={field.name}
            label={field.label}
            hint={field.required ? undefined : "Optional"}
            invalid={badField === field.name}
            wide={field.kind === "textarea"}
          >
            {field.kind === "select" ? (
              <select value={values[field.name] ?? ""} onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}>
                {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            ) : field.kind === "textarea" ? (
              <textarea
                rows={4}
                spellCheck={false}
                aria-invalid={badField === field.name}
                value={values[field.name] ?? ""}
                placeholder={field.placeholder ?? undefined}
                onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
              />
            ) : (
              <input
                type={field.kind === "password" ? "password" : field.kind === "number" ? "number" : "text"}
                autoComplete={field.kind === "password" ? "new-password" : "off"}
                required={field.required}
                aria-invalid={badField === field.name}
                value={values[field.name] ?? ""}
                placeholder={field.placeholder ?? undefined}
                onChange={(event) => setValues({ ...values, [field.name]: event.target.value })}
              />
            )}
          </Field>
        ))}

        <p className="form-note">
          Use a user that can only read. Run a preflight check first; the connection can be saved once it passes.
          The password is encrypted at rest and never shown again.
        </p>

        {error && <div className="callout callout-danger" role="alert"><strong>{error}</strong></div>}
        {current && !current.ok && (
          <div className="callout callout-danger" role="alert">
            <strong>{current.message}</strong>
            {current.detail && <code className="callout-detail">{current.detail}</code>}
          </div>
        )}
        {current?.ok && (
          <div className="callout callout-success" role="status">
            <strong>Connection OK.</strong> {current.message}
            {current.server_version && ` Server ${current.server_version}, ${formatMs(current.latency_ms)}.`}
          </div>
        )}
        {stale && <p className="form-note">Settings changed since the last preflight. Run it again before saving.</p>}

        <div className="row-actions">
          <button type="button" className="button ghost" disabled={missing || busy !== null} onClick={runPreflight}>
            {busy === "test" ? "Checking" : "Run preflight"}
          </button>
          <button type="submit" className="button primary" disabled={!current?.ok || busy !== null}>
            {busy === "save" ? "Saving" : "Save connection"}
          </button>
          <button type="button" className="button ghost" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </section>
  );
}

function ConnectorPicker({
  connectors,
  onPick,
  onCancel,
  onDriverInstalled,
}: {
  connectors: ConnectorType[];
  onPick: (kind: string) => void;
  onCancel: () => void;
  onDriverInstalled: () => void;
}) {
  const [installing, setInstalling] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, DriverInstallResult | string>>({});

  const install = (kind: string) => {
    setInstalling(kind);
    void api
      .installDriver(kind)
      .then((result) => {
        setResults((current) => ({ ...current, [kind]: result }));
        if (result.ok) onDriverInstalled();
      })
      .catch((cause: Error) => setResults((current) => ({ ...current, [kind]: cause.message })))
      .finally(() => setInstalling(null));
  };

  return (
    <section className="panel" aria-label="Choose a database type">
      <div className="panel-head">
        <h2 className="panel-heading">Choose a database type</h2>
        <button type="button" className="button ghost small" onClick={onCancel}>Cancel</button>
      </div>
      <ul className="connector-grid">
        {connectors.map((item) => {
          const result = results[item.kind];
          return (
            <li key={item.kind} className={item.available ? "connector" : "connector connector-off"}>
              <span className="connector-name">{item.label}</span>
              <span className="connector-desc">{item.description}</span>
              {item.available ? (
                <button type="button" className="button ghost small" onClick={() => onPick(item.kind)}>Connect</button>
              ) : item.installable ? (
                <button
                  type="button"
                  className="button ghost small"
                  disabled={installing !== null}
                  onClick={() => install(item.kind)}
                >
                  {installing === item.kind ? "Installing driver" : "Install driver"}
                </button>
              ) : (
                <span className="connector-note">{item.unavailable_reason}</span>
              )}
              {typeof result === "string" && <span className="connector-error">{result}</span>}
              {result && typeof result !== "string" && !result.ok && (
                <span className="connector-error" title={result.log_tail}>{result.message}</span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Field({
  label,
  hint,
  invalid,
  wide,
  children,
}: {
  label: string;
  hint?: string;
  invalid?: boolean;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <label className={`field${invalid ? " field-invalid" : ""}${wide ? " field-wide" : ""}`}>
      <span className="field-label">
        {label}
        {hint && <span className="field-hint">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function defaults(connector: ConnectorType | null): Record<string, string> {
  if (!connector) return {};
  const values: Record<string, string> = {};
  for (const field of connector.fields) {
    if (field.name === "port" && connector.default_port) values.port = String(connector.default_port);
    else if (field.default) values[field.name] = field.default;
  }
  return values;
}
