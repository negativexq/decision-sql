import { useState } from "react";
import type { RunRecord, RunSummary, TraceStage } from "../types";
import { StatusBadge } from "../components/StatusBadge";
import { TraceTimeline } from "../components/TraceTimeline";

export function Traces({ runs, selected, onSelect }: { runs: RunSummary[]; selected: RunRecord | null; onSelect: (run: RunSummary) => void }) {
  const current = selected;
  const [stage, setStage] = useState<TraceStage | null>(null);
  return <div className="page-stack"><section className="page-intro"><div><span className="eyebrow accent">OBSERVABILITY</span><h2>Trace explorer</h2><p>Inspect real stage outcomes emitted by the Decision-SQL runtime.</p></div></section><div className="trace-explorer"><section className="card trace-run-list"><span className="eyebrow">RUNS</span>{runs.map((run) => <button className={current?.run_id === run.run_id ? "selected" : ""} key={run.run_id} onClick={() => { onSelect(run); setStage(null); }}><span><code>{run.run_id}</code><small>{run.question}</small></span><StatusBadge value={run.runtime_outcome} /></button>)}{!runs.length && <p className="small-muted">Run a query in Playground first.</p>}</section>{current ? <section className="card trace-detail"><div className="section-heading"><div><span className="eyebrow">{current.run_id}</span><h3>{current.question}</h3></div><StatusBadge value={current.runtime_outcome} /></div><div className="trace-layout"><div><TraceTimeline trace={current.trace} onStage={setStage} /></div>{stage ? <StageDetail stage={stage} /> : <div className="stage-placeholder">Select a stage to inspect its bounded evidence.</div>}</div><div className="events"><div className="section-heading"><h3>Events</h3><span className="small-muted">structured runtime timeline</span></div>{current.trace.events.map((event) => <div className="event-row" key={event.event_id}><time>{new Date(event.at).toLocaleTimeString([], { hour12: false, fractionalSecondDigits: 3 })}</time><code>{event.event_type}</code><span>{event.message}</span></div>)}</div></section> : <div className="empty-state"><h3>Select a run to inspect its trace</h3></div>}</div></div>;
}

function StageDetail({ stage }: { stage: TraceStage }) {
  return <aside className="stage-detail"><div className="stage-detail-head"><span className="eyebrow">STAGE DETAIL</span><StatusBadge value={stage.status} /></div><h3>{stage.label}</h3><p>{stage.reason ?? "Completed without a rejection reason."}</p><dl>{Object.entries(stage.metadata).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></div>)}<div><dt>Duration</dt><dd>{stage.duration_ms == null ? "Not recorded" : `${stage.duration_ms.toFixed(1)} ms`}</dd></div></dl></aside>;
}
