import { useCallback, useEffect, useState } from "react";
import { api, getActor, setActor as storeActor } from "./api/client";
import { Sidebar, type View } from "./components/Sidebar";
import { Ask } from "./pages/Ask";
import { Audit } from "./pages/Audit";
import { Connections } from "./pages/Connections";
import { Sources } from "./pages/Sources";
import type { ConnectionSummary, Preset, RunRecord, RunSummary, SchemaResponse } from "./types";

const SOURCE_KEY = "decision-sql.source";

function storedSource(): string {
  try {
    return window.localStorage.getItem(SOURCE_KEY) ?? "governed";
  } catch {
    return "governed";
  }
}

export default function App() {
  const [view, setView] = useState<View>("ask");
  const [actor, setActorState] = useState(getActor);
  const [auditKey, setAuditKey] = useState(0);
  const [run, setRun] = useState<RunRecord | null>(null);
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [focusKey, setFocusKey] = useState(0);
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [sourceId, setSourceId] = useState(storedSource);

  const chooseSource = (id: string) => {
    setSourceId(id);
    try {
      window.localStorage.setItem(SOURCE_KEY, id);
    } catch {
      // Remembering the choice is a convenience only.
    }
  };

  // Refresh whenever the user comes back to Ask, so new connections show up.
  useEffect(() => {
    if (view !== "ask") return;
    void api.connections().then(setConnections).catch(() => setConnections([]));
  }, [view]);

  useEffect(() => {
    void api.schema().then(setSchema).catch(() => setSchema(null));
    void api.presets().then(setPresets).catch(() => setPresets([]));
  }, []);

  const newQuestion = useCallback(() => {
    setRun(null);
    setError(null);
    setView("ask");
    setFocusKey((key) => key + 1);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.key.toLowerCase() === "n" && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        newQuestion();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [newQuestion]);

  const submit = async (question: string, presetId?: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.query(question, presetId, sourceId);
      setRun(result);
      setAuditKey((key) => key + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Check that the API is running.");
    } finally {
      setLoading(false);
    }
  };

  const open = (summary: RunSummary) => {
    void api.run(summary.run_id).then((record) => {
      setRun(record);
      setView("ask");
    });
  };

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <Sidebar
        view={view}
        onNavigate={setView}
        onNewQuestion={newQuestion}
        schema={schema}
        actor={actor}
        onActorChange={(value) => {
          storeActor(value);
          setActorState(value);
        }}
      />
      <main id="main" className="main" tabIndex={-1}>
        {view === "ask" && (
          <Ask
            run={run}
            presets={presets}
            loading={loading}
            error={error}
            onSubmit={(q, p) => void submit(q, p)}
            focusKey={focusKey}
            connections={connections}
            sourceId={sourceId}
            onSourceChange={chooseSource}
            onManageConnections={() => setView("connections")}
          />
        )}
        {view === "audit" && <Audit actor={actor} refreshKey={auditKey} onOpen={open} />}
        {view === "connections" && <Connections />}
        {view === "sources" && <Sources schema={schema} />}
      </main>
    </div>
  );
}
