import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Preset, RunRecord, TraceStage } from "../types";
import { ResultTable } from "../components/ResultTable";
import { SqlViewer } from "../components/SqlViewer";
import { StatusBadge } from "../components/StatusBadge";
import { TraceTimeline } from "../components/TraceTimeline";

export function Playground({ onRun }: { onRun: (run: RunRecord) => void }) {
  const [question, setQuestion] = useState("");
  const [preset, setPreset] = useState<string | undefined>();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [run, setRun] = useState<RunRecord | null>(null);
  const [selectedStage, setSelectedStage] = useState<TraceStage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { void api.presets().then(setPresets).catch(() => setPresets([])); }, []);
  const submit = async (presetId?: string) => {
    setLoading(true); setError(null);
    try { const result = await api.query(presetId ? (presets.find((item) => item.id === presetId)?.question ?? question) : question, presetId); setRun(result); onRun(result); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Request failed"); }
    finally { setLoading(false); }
  };
  return (
    <div className="page-grid">
      <section className="hero-panel"><div><span className="eyebrow accent">GOVERNED REQUEST WORKSPACE</span><h2>Ask your governed data.</h2><p>One proposal enters the same deterministic gates used by the product runtime.</p></div><div className="hero-meta"><span>LOCAL DEMO</span><span>READ ONLY</span><span>ONE CALL</span><div className="path-ribbon"><span>Question</span><i>→</i><span>Decision</span><i>→</i><span>SQL</span><i>→</i><span>Gates</span><i>→</i><span>Result</span></div></div></section>
      <section className="composer card"><div className="section-heading"><div><span className="eyebrow">PLAYGROUND</span><h3>Natural-language request</h3></div><span className="small-muted">No raw SQL input</span></div><label className="field-label" htmlFor="question">Question</label><textarea id="question" value={question} onChange={(event) => { setQuestion(event.target.value); setPreset(undefined); }} placeholder="e.g. Show total order revenue by status" aria-busy={loading} /><div className="composer-footer"><div className="preset-list">{presets.map((item) => <button type="button" className={`preset ${preset === item.id ? "selected" : ""}`} key={item.id} onClick={() => { setPreset(item.id); setQuestion(item.question); }}><span>{item.category.replaceAll("_", " ")}</span>{item.label}</button>)}</div><div className="actions"><button type="button" className="button secondary" onClick={() => { setQuestion(""); setPreset(undefined); setRun(null); }}>Clear</button><button type="button" className="button primary" disabled={!question.trim() || loading} onClick={() => void submit(preset)}>{loading ? "Running…" : "Run query"}<span>↗</span></button></div></div>{error && <div className="error-callout" role="alert">{error}</div>}</section>
      {run && <><section className="summary-grid"><div className="metric card"><span className="metric-label">Model decision</span><StatusBadge value={run.model_decision ?? "NO_DECISION"} /><strong>{run.model_decision ? (run.model_decision === "ANSWER" ? "Proposal admitted" : "SQL runtime not entered") : "Generation did not produce a decision"}</strong>{run.model_reason_code && <code className="reason-code">{run.model_reason_code}</code>}</div><div className="metric card"><span className="metric-label">Runtime outcome</span><StatusBadge value={run.runtime_outcome} /><strong>{run.runtime_reason ?? run.runtime_outcome.replaceAll("_", " ")}</strong></div><div className="metric card"><span className="metric-label">Latency</span><strong className="metric-number">{run.duration_ms?.toFixed(0) ?? "—"}<small> ms</small></strong><span className="metric-sub">request lifecycle</span></div><div className="metric card"><span className="metric-label">Rows</span><strong className="metric-number">{run.row_count}</strong><span className="metric-sub">bounded result</span></div></section>
        <section className="card sql-card"><div className="section-heading"><div><span className="eyebrow">PROPOSED SQL</span><h3>{run.proposed_sql ? "Untrusted provider output" : "No SQL proposed"}</h3></div><span className={`source-badge ${run.proposal_source.toLowerCase()}`}>{run.proposal_source.replaceAll("_", " ")}</span></div>{run.replay_notice && <p className="replay-notice">{run.replay_notice}</p>}<SqlViewer sql={run.proposed_sql} /></section>
        <section className="card trace-card"><div className="section-heading"><div><span className="eyebrow">DETERMINISTIC TRACE</span><h3>Every gate has an observable outcome</h3></div><span className="trace-id">{run.trace_id}</span></div><div className="trace-layout"><TraceTimeline trace={run.trace} onStage={setSelectedStage} />{selectedStage ? <StageDetail stage={selectedStage} /> : <div className="stage-placeholder">Select a stage to inspect its bounded evidence.</div>}</div></section>
        {run.runtime_outcome === "EXECUTED" && <section className="card result-card"><div className="section-heading"><div><span className="eyebrow">RESULT</span><h3>Bounded database response</h3></div><span className="small-muted">{run.row_count} rows</span></div><ResultTable columns={run.columns} rows={run.rows} truncated={run.truncated} /></section>}
      </>}
      {!run && <div className="empty-state"><div className="empty-icon" aria-hidden="true" /><h3>Ready for a governed request</h3><p>Ask a question or choose a demo scenario to inspect the complete lifecycle.</p></div>}
    </div>
  );
}
function StageDetail({ stage }: { stage: TraceStage }) {
  return <aside className="stage-detail"><div className="stage-detail-head"><span className="eyebrow">STAGE DETAIL</span><StatusBadge value={stage.status} /></div><h3>{stage.label}</h3><p>{stage.reason ?? "Completed without a rejection reason."}</p><dl>{Object.entries(stage.metadata).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></div>)}<div><dt>Duration</dt><dd>{stage.duration_ms == null ? "Not recorded" : `${stage.duration_ms.toFixed(1)} ms`}</dd></div></dl></aside>;
}
