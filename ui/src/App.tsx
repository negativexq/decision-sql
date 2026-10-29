import { useCallback, useEffect, useState } from "react";
import { api, getActor, setActor as storeActor } from "./api/client";
import { Sidebar, type View } from "./components/Sidebar";
import { Ask, nextProgress, type Progress } from "./pages/Ask";
import { Audit } from "./pages/Audit";
import { Connections } from "./pages/Connections";
import { Reports } from "./pages/Reports";
import { Sources } from "./pages/Sources";
import type { ConditionEdit, ConnectionSummary, Preset, RunRecord, RunSummary, SchemaResponse } from "./types";

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
  // The questions of one conversation, oldest first; a follow-up builds on the last.
  const [thread, setThread] = useState<RunRecord[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [recent, setRecent] = useState<RunSummary[]>([]);
  const [schema, setSchema] = useState<SchemaResponse | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
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

  // The user's own recent questions, for the sidebar; refreshed after each run.
  useEffect(() => {
    void api
      .runs({ actor: actor.trim().toLowerCase() || "anonymous", limit: 60 })
      .then((result) => setRecent(result.runs))
      .catch(() => setRecent([]));
  }, [actor, auditKey]);

  const newQuestion = useCallback(() => {
    setThread([]);
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

  const submit = async (question: string, presetId?: string, followUpOf?: string) => {
    if (progress || editing) return;
    setError(null);
    setProgress({ question, startedAt: Date.now(), stages: [], working: null, modelCalls: 0 });
    try {
      const result = await api.ask(
        {
          question,
          connection_id: sourceId,
          ...(presetId ? { preset_id: presetId } : {}),
          ...(followUpOf ? { follow_up_of: followUpOf } : {}),
        },
        (event) => setProgress((current) => (current ? nextProgress(current, event) : current)),
      );
      setThread((current) => (followUpOf ? [...current, result] : [result]));
      setAuditKey((key) => key + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Check that the API is running.");
    } finally {
      setProgress(null);
    }
  };

  const edit = async (run: RunRecord, edits: ConditionEdit[]) => {
    if (progress || editing) return;
    setEditing(run.run_id);
    setError(null);
    try {
      const result = await api.editRun(run.run_id, edits);
      setThread((current) => [...current, result]);
      setAuditKey((key) => key + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Check that the API is running.");
    } finally {
      setEditing(null);
    }
  };

  const open = (summary: Pick<RunSummary, "run_id">) => {
    void api.thread(summary.run_id).then((records) => {
      const last = records[records.length - 1];
      if (!last) return;
      // Follow-ups from this run (rerun, picking an interpretation) must ask the same source.
      chooseSource(last.connection_id);
      setThread(records);
      setError(null);
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
        recent={recent}
        current={thread[thread.length - 1]?.run_id ?? null}
        onOpenRecent={open}
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
            thread={thread}
            presets={presets}
            progress={progress}
            error={error}
            onSubmit={(q, p, f) => void submit(q, p, f)}
            onEdit={(run, edits) => void edit(run, edits)}
            editing={editing}
            focusKey={focusKey}
            connections={connections}
            sourceId={sourceId}
            onSourceChange={chooseSource}
            onManageConnections={() => setView("connections")}
          />
        )}
        {view === "reports" && <Reports />}
        {view === "audit" && <Audit actor={actor} refreshKey={auditKey} onOpen={open} />}
        {view === "connections" && <Connections />}
        {view === "sources" && <Sources schema={schema} />}
      </main>
    </div>
  );
}
