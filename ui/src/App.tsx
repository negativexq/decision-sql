import { useCallback, useEffect, useMemo, useState } from "react";
import { api, getActor, setActor as storeActor } from "./api/client";
import type { AddTarget } from "./components/AddToReport";
import { Library, type LibraryItem, Sidebar, type View, conversations } from "./components/Sidebar";
import { Ask, nextProgress, type Progress } from "./pages/Ask";
import { Audit } from "./pages/Audit";
import { Connections } from "./pages/Connections";
import { Reports } from "./pages/Reports";
import { Sources } from "./pages/Sources";
import type { ChartType, ConditionEdit, ConnectionSummary, Mark, Preset, ReportSummary, RunRecord, RunSummary, SchemaResponse } from "./types";

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
  const [marks, setMarks] = useState<Mark[]>([]);
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [reportId, setReportId] = useState<string | null>(null);
  // Questions of pinned conversations that fell out of the recent list.
  const [pinnedQuestions, setPinnedQuestions] = useState<Record<string, string>>({});
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
      .runs({ actor: actor.trim().toLowerCase() || "anonymous", limit: 200 })
      .then((result) => setRecent(result.runs))
      .catch(() => setRecent([]));
  }, [actor, auditKey]);

  // Names, pins and reports change from several places; reload them together.
  const [libraryKey, setLibraryKey] = useState(0);
  useEffect(() => {
    void api.marks().then(setMarks).catch(() => setMarks([]));
    void api.reports().then(setReports).catch(() => setReports([]));
  }, [actor, libraryKey, auditKey]);

  const threads = useMemo(() => conversations(recent), [recent]);

  useEffect(() => {
    const known = new Set(threads.map((item) => item.root));
    const missing = marks.filter(
      (mark) => mark.kind === "conversation" && mark.pinned_at && !mark.title && !known.has(mark.item_id) && !(mark.item_id in pinnedQuestions),
    );
    for (const mark of missing) {
      void api
        .run(mark.item_id)
        .then((record) => setPinnedQuestions((current) => ({ ...current, [mark.item_id]: record.question })))
        .catch(() => setPinnedQuestions((current) => ({ ...current, [mark.item_id]: "Conversation" })));
    }
  }, [marks, threads, pinnedQuestions]);

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

  const markOf = (kind: Mark["kind"], id: string) => marks.find((mark) => mark.kind === kind && mark.item_id === id);
  const conversationItems: LibraryItem[] = threads.map((item) => {
    const mark = markOf("conversation", item.root);
    return {
      kind: "conversation",
      id: item.root,
      title: mark?.title ?? item.question,
      pinned: Boolean(mark?.pinned_at),
      meta: item.turns > 1 ? String(item.turns) : undefined,
      canRename: true,
    };
  });
  const listed = new Set(conversationItems.map((item) => item.id));
  const pinnedElsewhere: LibraryItem[] = marks
    .filter((mark) => mark.kind === "conversation" && mark.pinned_at && !listed.has(mark.item_id))
    .map((mark) => ({
      kind: "conversation",
      id: mark.item_id,
      title: mark.title ?? pinnedQuestions[mark.item_id] ?? "Conversation",
      pinned: true,
      canRename: true,
    }));
  const reportItems: LibraryItem[] = reports.map((report) => {
    const mark = markOf("report", report.id);
    return {
      kind: "report",
      id: report.id,
      title: report.title,
      pinned: Boolean(mark?.pinned_at),
      meta: report.card_count ? String(report.card_count) : undefined,
      canRename: report.role !== "viewer",
    };
  });
  const pinnedAt = (item: LibraryItem) => markOf(item.kind, item.id)?.pinned_at ?? "";
  const pinnedItems = [...conversationItems, ...pinnedElsewhere, ...reportItems]
    .filter((item) => item.pinned)
    .sort((a, b) => pinnedAt(b).localeCompare(pinnedAt(a)));
  const currentItem = view === "reports" ? reportId : thread[0]?.run_id ?? null;

  const pin = (item: LibraryItem, pinned: boolean) => {
    const mark = markOf(item.kind, item.id);
    void api
      .setMark(item.kind, item.id, { title: mark?.title ?? null, pinned })
      .then(() => setLibraryKey((key) => key + 1))
      .catch((cause: Error) => setError(cause.message));
  };
  const rename = (item: LibraryItem, title: string) => {
    const done = () => setLibraryKey((key) => key + 1);
    if (item.kind === "report") {
      if (!title) return;
      void api.renameReport(item.id, title).then(done).catch((cause: Error) => setError(cause.message));
      return;
    }
    // A blank name goes back to the conversation's first question.
    const mark = markOf(item.kind, item.id);
    void api
      .setMark(item.kind, item.id, { title: title || null, pinned: Boolean(mark?.pinned_at) })
      .then(done)
      .catch((cause: Error) => setError(cause.message));
  };
  const openItem = (item: LibraryItem) => {
    if (item.kind === "report") {
      setReportId(item.id);
      setView("reports");
      return;
    }
    open({ run_id: item.id });
  };
  const addToReport = async (run: RunRecord, target: AddTarget, chart: ChartType) => {
    const id = "reportId" in target ? target.reportId : (await api.createReport(target.title, run.connection_id)).id;
    const report = await api.addCard(id, { run_id: run.run_id, chart });
    setLibraryKey((key) => key + 1);
    return report;
  };

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <Sidebar
        view={view}
        onNavigate={(next) => {
          if (next === "reports") setReportId(null);
          setView(next);
        }}
        onNewQuestion={newQuestion}
        library={
          <Library
            pinned={pinnedItems}
            recent={conversationItems.filter((item) => !item.pinned)}
            reports={reportItems.filter((item) => !item.pinned)}
            current={currentItem}
            onOpen={openItem}
            onPin={pin}
            onRename={rename}
            onAllReports={() => {
              setReportId(null);
              setView("reports");
            }}
          />
        }
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
            reports={reports}
            onAddToReport={addToReport}
            onOpenReport={(id) => {
              setReportId(id);
              setView("reports");
            }}
          />
        )}
        {view === "reports" && (
          <Reports
            openId={reportId}
            onOpen={(id) => {
              setReportId(id);
              if (id === null) setLibraryKey((key) => key + 1);
            }}
          />
        )}
        {view === "audit" && <Audit actor={actor} refreshKey={auditKey} onOpen={open} />}
        {view === "connections" && <Connections />}
        {view === "sources" && <Sources schema={schema} />}
      </main>
    </div>
  );
}
