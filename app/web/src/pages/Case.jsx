import { useState } from "react";
import { api } from "../api.js";
import { clock, inr, when } from "../format.js";
import { useLoad } from "../hooks.js";
import AccountPanel from "../components/AccountPanel.jsx";
import { Failed, Loading, MoneyBar, NightBadge, Stmt } from "../components/bits.jsx";
import Trail from "../components/Trail.jsx";

const TABS = [["trail", "Money trail"], ["log", "Agent log"], ["paperwork", "Paperwork"]];

export default function Case({ caseId, accountId, officer }) {
  const detail = useLoad(() => api.caseDetail(caseId), [caseId]);
  const [tab, setTab] = useState("trail");
  const select = (acc) => { window.location.hash = `#/case/${caseId}/${encodeURIComponent(acc)}`; };
  const close = () => { window.location.hash = `#/case/${caseId}`; };

  if (detail.loading && !detail.data) return <Loading what={`Loading ${caseId}`} />;
  if (detail.error) return <Failed error={detail.error} retry={detail.reload} />;
  const c = detail.data;
  const worked = c.ticket?.ticket_status && c.ticket.ticket_status !== "QUEUED";

  return (
    <div className={accountId ? "with-panel" : ""}>
      <div className="case-main">
        <header className="page-head">
          <div>
            <p className="crumb"><a href="#/review">Morning review</a> / {caseId}</p>
            <h1>{inr(c.amount_inr)} reported, UTR <span className="ident">{c.utr}</span></h1>
            <p className="lede">
              Ticket landed {when(c.ticket?.received_at)} <NightBadge on={c.after_hours} />
              {c.run?.sim_holds_at && <>; required holds landed {clock(c.run.sim_holds_at)}, {Math.round((new Date(c.run.sim_holds_at.replace(" ", "T")) - new Date(c.ticket.received_at.replace(" ", "T"))) / 60000)} minutes later</>}.
              {" "}{c.ordering_authority}, ref. {c.ordering_reference || "–"}; {c.notice_legal_basis || "no legal basis given"}.
            </p>
          </div>
          {c.run && <a className="btn" href={`#/compare/${caseId}`}>Compare with today's desk</a>}
        </header>

        {!worked && <p className="empty">The agent has not worked this ticket yet. Open the ticket queue and work it.</p>}
        {c.check?.notice_status === "INCOMPLETE" && <p className="warn">Incomplete notice: {c.check.problems}</p>}
        {c.run?.summary_text && (
          <section className="summary">
            <h2 className="sub">Case summary for the morning review</h2>
            <p>{c.run.summary_text}</p>
            <p className="quiet">Written by {c.run.run_mode === "llm" ? `the local model (${c.run.model_name}), numbers checked against the database` : "the agent's fixed template"}. {c.checks.length > 0 && (c.checks.every((k) => k.check_passed) ? `All ${c.checks.length} checks passed before anything was held.` : "The checker stopped the automatic holds.")}</p>
          </section>
        )}

        {worked && (
          <>
            <div className="tabs" role="tablist">
              {TABS.map(([key, label]) => (
                <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "tab on" : "tab"} onClick={() => setTab(key)}>{label}</button>
              ))}
            </div>
            {tab === "trail" && <TrailTab caseId={caseId} selected={accountId} onSelect={select} paid={c.paid_inr} />}
            {tab === "log" && <LogTab caseId={caseId} />}
            {tab === "paperwork" && <PaperworkTab caseId={caseId} officer={officer} />}
          </>
        )}
      </div>
      {accountId && <AccountPanel caseId={caseId} accountId={accountId} officer={officer} onClose={close} onChanged={detail.reload} />}
    </div>
  );
}

function TrailTab({ caseId, selected, onSelect, paid }) {
  const trail = useLoad(() => api.trail(caseId), [caseId]);
  const acts = useLoad(() => api.actions(caseId), [caseId]);
  if ((trail.loading && !trail.data) || (acts.loading && !acts.data)) return <Loading what="Drawing the money trail" />;
  if (trail.error) return <Failed error={trail.error} retry={trail.reload} />;
  if (acts.error) return <Failed error={acts.error} retry={acts.reload} />;
  const a = acts.data;
  const sum = (code) => a.filter((x) => x.action_code === code).reduce((s, x) => s + Math.min(x.victim_amount_inr ?? x.amount_inr ?? 0, x.amount_inr ?? 0), 0);
  const held = sum("MANDATORY_HOLD");
  const exited = a.filter((x) => x.action_code === "RECORD_EXIT").reduce((s, x) => s + (x.amount_inr || 0), 0);
  const onward = a.filter((x) => x.action_code === "ONWARD_ENTRY").reduce((s, x) => s + (x.amount_inr || 0), 0);
  return (
    <>
      <MoneyBar total={paid} caption="Where the victim's money was when the holds landed" parts={[
        { key: "victim", label: "Held here", value: held },
        { key: "onward", label: "Moved to other banks (entries filed)", value: onward },
        { key: "gone", label: "Left banking (exits filed)", value: exited },
        { key: "rest", label: "Elsewhere on the trail", value: Math.max(0, paid - held - onward - exited) },
      ]} />
      <Trail trail={trail.data} selected={selected} onSelect={onSelect} />
      <p className="quiet">Click an account to see whose money is in it, what the agent did, and what is waiting for you. Edge labels show how much of each payment was the victim's money (lowest-balance rule).</p>
    </>
  );
}

function LogTab({ caseId }) {
  const { data, error, loading, reload } = useLoad(() => api.log(caseId), [caseId]);
  if (loading && !data) return <Loading what="Loading the agent log" />;
  if (error) return <Failed error={error} retry={reload} />;
  return data.map((run) => (
    <section key={run.run_id} className="log">
      <h3 className="sub">Run {run.run_id}: {run.run_status?.toLowerCase()}, {run.run_mode === "llm" ? `a local model chose the order (${run.model_name})` : "fixed order"}, {Number(run.wall_seconds || 0).toFixed(1)} s</h3>
      <ol className="steps">
        {run.steps.map((s) => (
          <li key={s.step_no} className={`step ${s.can_change ? "step-change" : ""} by-${s.chosen_by}`}>
            <span className="step-tool">{s.tool_name.replaceAll("_", " ")}</span>
            <span className="step-by">{s.chosen_by === "runtime" ? "always runs" : s.chosen_by === "llm" ? "model chose" : s.chosen_by === "fallback" ? "fixed order took over" : "fixed order"}</span>
            <span className="step-text">{s.result_text}</span>
            <span className="step-meta"><Stmt ids={s.stmt_ids} /> {s.wall_ms} ms</span>
          </li>
        ))}
      </ol>
    </section>
  ));
}

function PaperworkTab({ caseId, officer }) {
  const { data, error, loading, reload } = useLoad(() => api.paperwork(caseId), [caseId]);
  const [err, setErr] = useState(null);
  if (loading && !data) return <Loading what="Loading the paperwork" />;
  if (error) return <Failed error={error} retry={reload} />;
  const send = async (id) => {
    setErr(null);
    if (!officer.trim()) return setErr("Enter your name in the sidebar first.");
    try { await api.sendKyc(caseId, id, { officer }); reload(); } catch (e) { setErr(e.message); }
  };
  const kinds = [["ONWARD", "Onward entries (each opens a ticket at the next bank)"], ["EXIT", "Exits (money that left banking)"], ["KYC", "KYC packets (due within a week)"]];
  return (
    <>
      {err && <p className="error" role="alert">{err}</p>}
      {kinds.map(([kind, title]) => {
        const list = data.filter((e) => e.entry_kind === kind);
        if (!list.length) return null;
        return (
          <section key={kind}>
            <h3 className="sub">{title}</h3>
            <div className="table-wrap">
              <table className="table">
                <tbody>
                  {list.map((e) => (
                    <tr key={e.entry_id}>
                      {kind === "KYC" ? (
                        <td colSpan="3"><b>{e.account_id}</b><br /><span className="kyc">{e.detail}</span></td>
                      ) : (
                        <>
                          <td><b>{kind === "ONWARD" ? `${e.bank_name}, ${e.account_id}` : e.detail}</b><br /><span className="ident">{e.utr}</span> from {e.from_account} at {clock(e.moved_at)}</td>
                          <td className="num">{inr(e.amount_inr)} moved</td>
                          <td className="num">{inr(e.victim_amount_inr)} of it the victim's</td>
                        </>
                      )}
                      <td>{e.entry_status === "FILED" ? `Filed ${clock(e.filed_at)} by ${e.filed_by}` : <button className="btn" onClick={() => send(e.entry_id)}>Send to CFCFRMS</button>} <Stmt ids={e.stmt_id} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </>
  );
}