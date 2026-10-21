import { useState } from "react";
import { api } from "../api.js";
import Decide from "../components/Decide.jsx";
import { Failed, Loading, Stmt } from "../components/bits.jsx";
import { evidenceRows, evidenceTitle } from "../evidence.js";
import { age, clock, inr, plural, when } from "../format.js";
import { useLoad } from "../hooks.js";

const OPEN = ["PROPOSED", "WAITING"];
const short = (name) => String(name || "").replace(/\s*\(.*\)$/, "");
const joinAnd = (xs) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);

export default function Case({ caseId, accountId, officer }) {
  const [v, setV] = useState(0);
  const bump = () => setV((x) => x + 1);
  const detail = useLoad(() => api.caseDetail(caseId), [caseId, v]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  if (detail.loading && !detail.data) return <div className="wrap page"><Loading what={`Loading ${caseId}`} /></div>;
  if (detail.error) return <div className="wrap page"><Failed error={detail.error} retry={detail.reload} /></div>;
  const c = detail.data;
  const status = c.ticket?.ticket_status;
  const run = c.run;
  const worked = status && status !== "QUEUED";
  const traced = run && ["DONE", "CHECK_FAILED"].includes(run.run_status);

  async function workNow() {
    setBusy(true);
    setErr(null);
    try { await api.workOne(caseId); bump(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <main id="main" className="wrap page">
      <nav className="crumb" aria-label="Breadcrumb"><a href="#/review">Review</a> / <a href="#/queue">Tickets</a> / {clock(c.ticket?.received_at || c.reported_at)} ticket</nav>
      <div className="pagehead">
        <div>
          <h1>{inr(c.amount_inr)} reported at {clock(c.ticket?.received_at || c.reported_at)}</h1>
          <div className="meta">
            {c.txn_at && <span>Paid {clock(c.txn_at)}{c.paid_channel ? ` by ${c.paid_channel}` : ""}</span>}
            <span className="mono">UTR {c.utr}</span>
            {c.ordering_authority && <span>{c.ordering_authority}{c.ordering_reference ? ` · ${c.ordering_reference}` : ""}</span>}
            <span>{c.notice_legal_basis ? `Notice under ${c.notice_legal_basis}` : "No legal basis given on the notice"}</span>
          </div>
        </div>
        <div className="row">
          {run?.sim_holds_at && traced && (
            <span className="pill pill-night">Holds in place {clock(run.sim_holds_at)}{c.after_hours ? ", before the desk opened" : ", 3 min after the ticket"}</span>
          )}
          {traced && <a className="btn btn-sm" href={`#/compare/${caseId}`}>Before / after</a>}
        </div>
      </div>

      {!worked && (
        <div className="card pad" style={{ marginBottom: 24 }}>
          <p style={{ marginBottom: 10 }}>The agent has not worked this ticket yet.</p>
          <button className="btn btn-primary" disabled={busy} onClick={workNow}>{busy ? "The agent is working it…" : "Let the agent work it now"}</button>
          {err && <p className="error" role="alert">{err}</p>}
        </div>
      )}
      {c.check?.notice_status === "INCOMPLETE" && <p className="status" role="status">Incomplete notice: {c.check.problems}</p>}
      {run && !traced && <p className="status" role="status">{run.summary_text}</p>}
      {run?.run_status === "CHECK_FAILED" && <p className="status" role="status">The checker stopped the automatic holds on this ticket. Nothing was held; the reasons are in the agent's log below.</p>}

      {traced && <Worked caseId={caseId} c={c} accountId={accountId} officer={officer} v={v} bump={bump} />}

      {worked && (
        <div className="more">
          <details>
            <summary>What the agent did, step by step</summary>
            <AgentLog caseId={caseId} v={v} />
          </details>
          {traced && (
            <details>
              <summary>Paperwork filed and drafted</summary>
              <Paperwork caseId={caseId} officer={officer} v={v} onDone={bump} />
            </details>
          )}
        </div>
      )}
    </main>
  );
}

function Worked({ caseId, c, accountId, officer, v, bump }) {
  const trail = useLoad(() => api.trail(caseId), [caseId, v]);
  const acts = useLoad(() => api.actions(caseId), [caseId, v]);
  if ((trail.loading && !trail.data) || (acts.loading && !acts.data)) return <Loading what="Drawing the money trail" />;
  if (trail.error) return <Failed error={trail.error} retry={trail.reload} />;
  if (acts.error) return <Failed error={acts.error} retry={acts.reload} />;
  const { nodes, edges } = trail.data;
  const actions = acts.data.map((a) => ({ ...a, case_id: caseId }));
  const waiting = actions.filter((a) => a.act_mode === "OFFICER" && OPEN.includes(a.action_status));
  const own = nodes.filter((n) => n.kind === "own");
  const selected = accountId && nodes.some((n) => n.id === accountId && n.kind !== "exit") ? accountId
    : (waiting.find((a) => a.action_code === "PROPOSE_NARROW") || waiting[0])?.account_id || own[0]?.id || null;
  return (
    <>
      <CaseStats actions={actions} edges={edges} run={c.run} waiting={waiting} />
      <div className="casegrid">
        <TrailCards c={c} nodes={nodes} edges={edges} actions={actions} selected={selected} caseId={caseId} />
        {selected && <AccountAside key={selected + v} caseId={caseId} node={nodes.find((n) => n.id === selected)} edges={edges} officer={officer} v={v} onDone={bump} />}
      </div>
    </>
  );
}

function CaseStats({ actions, edges, run, waiting }) {
  const of = (code) => actions.filter((a) => a.action_code === code);
  const holds = of("MANDATORY_HOLD");
  const held = holds.reduce((s, a) => s + (a.amount_inr || 0), 0);
  const heldVictim = holds.reduce((s, a) => s + Math.min(a.victim_amount_inr || 0, a.amount_inr || 0), 0);
  const onward = of("ONWARD_ENTRY");
  const banks = [...new Set(onward.map((a) => a.bank_name).filter(Boolean))];
  const exits = edges.filter((e) => e.is_exit);
  const exitTotal = of("RECORD_EXIT").reduce((s, a) => s + (a.amount_inr || 0), 0);
  const allCash = exits.length && exits.every((e) => e.channel === "ATM");
  const count = (code) => waiting.filter((a) => a.action_code === code).length;
  const waitParts = [
    count("PROPOSE_NARROW") && plural(count("PROPOSE_NARROW"), "hold to reduce", "holds to reduce"),
    count("PROPOSE_FULL_FREEZE") && plural(count("PROPOSE_FULL_FREEZE"), "account to freeze", "accounts to freeze"),
    count("CONFIRM_HOLD") && plural(count("CONFIRM_HOLD"), "hold to confirm", "holds to confirm"),
  ].filter(Boolean);
  return (
    <div className="stats">
      <div className="card pad stat">
        <div className="label">Held at Bank X</div>
        <div className="big">{inr(held)}</div>
        <div className="sub">{holds.length ? `on ${plural(holds.length, "account")}. ${heldVictim === held ? "All of it is the victim's." : `${inr(heldVictim)} is the victim's.`}` : "Nothing was left to hold."}</div>
      </div>
      <div className="card pad stat">
        <div className="label">Sent on to another bank</div>
        <div className="big">{inr(onward.reduce((s, a) => s + (a.amount_inr || 0), 0))}</div>
        <div className="sub">{onward.length ? `to ${joinAnd(banks)}. ${joinAnd(banks)} alerted at ${clock(run?.sim_holds_at)}.` : "Nothing moved to other banks."}</div>
      </div>
      <div className="card pad stat">
        <div className="label">{allCash ? "Withdrawn as cash" : "Left banking"}</div>
        <div className="big">{inr(exitTotal)}</div>
        <div className="sub">{exits.length ? (exits.length === 1 ? `${exits[0].channel === "ATM" ? "cash at " : ""}${exits[0].exit_identifier}, ${clock(exits[0].ts)}` : `${plural(exits.length, "withdrawal")}, reported to the police`) : "Nothing left banking."}</div>
      </div>
      <div className={`pad stat waitcard`}>
        <div className="label">Waiting for you</div>
        <div className="big">{waiting.length}</div>
        <div className="sub">{waitParts.length ? waitParts.join(", ") : "Nothing. The agent did all of it."}</div>
      </div>
    </div>
  );
}

function TrailCards({ c, nodes, edges, actions, selected, caseId }) {
  const label = Object.fromEntries(nodes.map((n) => [n.id, n.kind === "victim" ? "the victim" : n.kind === "exit" ? (n.id.startsWith("EXIT:ATM") ? "an ATM" : "a crypto wallet") : short(n.label)]));
  const hops = {};
  for (const n of nodes.filter((x) => x.kind !== "victim")) (hops[n.hop ?? 1] ||= []).push(n);
  return (
    <section className="trail" aria-labelledby="trail-h">
      <h2 id="trail-h">Where the {inr(c.paid_inr || c.amount_inr)} went</h2>
      <p>Read left to right. Each card says what it received, from whom, and what was done.</p>
      <div className="hops">
        {Object.keys(hops).sort((a, b) => a - b).map((h) => (
          <div className="hop" key={h}>
            <div className="hop-h">Hop {h}</div>
            {hops[h].map((n) => <HopCard key={n.id} n={n} edges={edges} actions={actions.filter((a) => a.account_id === n.id)} label={label} on={n.id === selected} caseId={caseId} />)}
          </div>
        ))}
      </div>
      <p className="note" style={{ marginTop: 14 }}>The victim's share is worked out by assuming each account spends its own money before the victim's.</p>
    </section>
  );
}

function HopCard({ n, edges, actions, label, on, caseId }) {
  const ins = edges.filter((e) => e.to_account === n.id);
  const outs = edges.filter((e) => e.from_account === n.id);
  const got = ins.length ? `Received ${inr(ins[0].amount_inr)} from ${label[ins[0].from_account]}, ${clock(ins[0].ts)}${ins.length > 1 ? `, and ${plural(ins.length - 1, "more payment")}` : ""}` : "";
  if (n.kind === "exit") {
    const isAtm = n.id.startsWith("EXIT:ATM");
    return (
      <div className="hopcard gone">
        <div className="top"><b>{isAtm ? `Cash, ${n.label.replace(/^ATM\s*/, "ATM ")}` : n.label}</b><span className="pill pill-gone">Gone</span></div>
        <div className="got">{got}. Reported to the police as {isAtm ? "cashed out" : "moved to crypto"}.</div>
      </div>
    );
  }
  const hold = actions.find((a) => a.action_code === "MANDATORY_HOLD");
  const marker = actions.find((a) => a.action_code === "HOLD_MARKER");
  const open = actions.filter((a) => a.act_mode === "OFFICER" && OPEN.includes(a.action_status));
  const decided = actions.filter((a) => a.act_mode === "OFFICER" && !OPEN.includes(a.action_status));
  const honest = n.evidence === "HONEST" || open.some((a) => a.action_code === "PROPOSE_NARROW");
  let pill;
  if (n.kind === "other") pill = <span className="pill pill-other">Other bank</span>;
  else if (honest) pill = <span className="pill pill-honest">Honest receiver</span>;
  else if (hold) pill = <span className="pill pill-held">Held</span>;
  else pill = <span className="pill pill-plain">{marker ? "Watched" : "Nothing held"}</span>;

  let did;
  if (n.kind === "other") {
    did = actions.some((a) => a.action_code === "ONWARD_ENTRY") ? `${n.bank_name} holds this one. Entry filed.` : `${n.bank_name}'s desk handles this one.`;
  } else {
    const sent = outs.length ? `Sent ${joinAnd(outs.map((e) => `${inr(e.amount_inr)} to ${label[e.to_account]}`))}. ` : "";
    let kept = "";
    if (hold) {
      const victim = Math.min(hold.victim_amount_inr + (hold.other_victims_inr || 0), hold.amount_inr);
      kept = <><b>{inr(hold.amount_inr)} held</b>, {victim >= hold.amount_inr ? "all the victim's." : `${inr(victim)} of it the victim's.`}</>;
    } else if (marker) kept = "Nothing left. Marked to catch new money.";
    else kept = "Nothing left to hold.";
    did = <>{sent}{kept}</>;
  }
  const nextText = open.map((a) => (a.action_code === "PROPOSE_FULL_FREEZE" ? (Number(a.balance_now_inr) ? "Freeze suggested" : "Watch suggested") : a.action_code === "PROPOSE_NARROW" ? "Smaller hold suggested" : "Confirmation needed")).join(" · ");
  const doneText = decided.map((a) => `${a.action_status === "REJECTED" ? "Kept as it was" : a.action_code === "PROPOSE_FULL_FREEZE" ? "Frozen" : a.action_code === "PROPOSE_NARROW" ? "Hold reduced" : "Hold confirmed"}`).join(" · ");
  return (
    <a className={`hopcard${on ? " on" : ""}`} href={`#/case/${caseId}/${encodeURIComponent(n.id)}`} aria-current={on ? "true" : undefined}>
      <div className="top"><b>{short(n.label)}{n.kind === "other" ? ` · ${n.bank_name}` : ""}</b>{pill}</div>
      <div className="got">{got}</div>
      <div className="did">{did}</div>
      {nextText && <div className="next">{nextText}{on ? " · details on the right" : ""}</div>}
      {!nextText && doneText && <div className="next decided">{doneText}</div>}
    </a>
  );
}

function headlineFor(p, name, panel) {
  if (!p) return null;
  if (p.action_code === "PROPOSE_NARROW") return `Hold only ${inr(p.amount_inr)} of ${name}'s money`;
  if (p.action_code === "PROPOSE_FULL_FREEZE") return Number(p.balance_now_inr) ? `Freeze ${name}'s whole account?` : `Watch ${name}'s account for new money?`;
  return `Confirm the ${inr(p.current_hold_inr ?? p.amount_inr)} hold on ${name}`;
}

function AccountAside({ caseId, node, edges, officer, v, onDone }) {
  const panel = useLoad(() => api.account(caseId, node.id), [caseId, node.id, v]);
  if (panel.loading && !panel.data) return <aside className="card aside"><Loading what="Loading the account" /></aside>;
  if (panel.error) return <aside className="card aside"><Failed error={panel.error} retry={panel.reload} /></aside>;
  const p = panel.data;
  const acc = p.account || {};
  const name = short(acc.holder_name || node.label);
  const actions = p.actions.map((a) => ({ ...a, case_id: caseId, account_id: node.id }));
  const open = actions.filter((a) => a.act_mode === "OFFICER" && OPEN.includes(a.action_status));
  const auto = actions.find((a) => a.act_mode === "AUTOMATIC");
  const lead = open[0];
  const sub = [acc.holder_name && acc.holder_name !== name ? acc.holder_name : name,
    `${acc.bank_name || node.bank_name || ""} ${acc.account_type || ""} account`.replace(/\s+/g, " ").trim()
      + (acc.opened_on ? `, open ${age(acc.opened_on, p.split?.as_of)}` : "")].join(", ");

  if (node.kind === "other") {
    const moved = edges.filter((e) => e.to_account === node.id);
    return (
      <aside className="card aside" aria-labelledby="acc-h">
        <div className="kicker done">Done by the agent</div>
        <h2 id="acc-h">{node.bank_name} was alerted</h2>
        <p className="sub">{sub}.</p>
        <dl className="money">
          {moved.map((e) => [<dt key={e.txn_id + "t"}>Moved {clock(e.ts)}</dt>, <dd key={e.txn_id + "d"}>{inr(e.amount_inr)}</dd>,
            <dt key={e.txn_id + "vt"} className="in victim">of which the victim's</dt>, <dd key={e.txn_id + "vd"} className="victim">{inr(e.victim_inr)}</dd>])}
        </dl>
        <p className="note">The onward entry opens a ticket at {node.bank_name}; their nodal desk places the hold there. Bank X cannot hold money in another bank.</p>
      </aside>
    );
  }

  const active = (p.holds || []).filter((h) => h.hold_status === "ACTIVE" && h.hold_kind !== "MARKER");
  const frozen = active.find((h) => h.hold_kind === "ACCOUNT_FREEZE");
  const heldNow = active.reduce((m, h) => Math.max(m, Number(h.amount_inr) || 0), 0);
  const since = active.map((h) => h.placed_at).sort()[0];
  const balance = Number(p.split?.balance_now_inr ?? node.balance_now_inr ?? 0);
  const victim = Math.min(Number(p.split?.victim_amount_inr || 0), heldNow);
  const others = Math.min(Number(p.split?.other_victims_inr || 0), Math.max(0, heldNow - victim));
  const ownPart = Math.max(0, heldNow - victim - others);
  const rows = evidenceRows(p.signals, acc.opened_on, p.split?.as_of);
  const basis = (lead || auto)?.legal_basis;

  return (
    <aside className="card aside" aria-labelledby="acc-h">
      {lead ? <div className="kicker">Waiting for you</div> : <div className="kicker done">{p.decisions?.length ? "Decided" : "Done by the agent"}</div>}
      <h2 id="acc-h">{lead ? headlineFor(lead, name) : frozen ? `${name}'s account is frozen` : heldNow ? `${inr(heldNow)} held on ${name}` : `Nothing held on ${name}`}</h2>
      <p className="sub">{sub}.</p>

      {lead?.action_code === "PROPOSE_FULL_FREEZE" && lead.explanation?.length > 0 && (
        <ul className="reasons">{lead.explanation.map((s) => <li key={s}>{s}</li>)}</ul>
      )}

      <h3>The money</h3>
      <dl className="money">
        <dt>Balance</dt><dd>{inr(balance)}</dd>
        <dt>{frozen ? "Frozen" : "Held"}{since ? ` since ${clock(since)}` : ""}</dt><dd>{inr(frozen ? balance : heldNow)}</dd>
        {heldNow > 0 && <><dt className="in victim">of which the victim's</dt><dd className="victim">{inr(victim)}</dd></>}
        {others > 0 && <><dt className="in others">of which other victims'</dt><dd className="others">{inr(others)}</dd></>}
        {heldNow > 0 && ownPart > 0 && <><dt className="in own">of which {acc.holder_type === "individual" ? "their" : "the holder's"} own</dt><dd className="own">{inr(ownPart)}</dd></>}
        <dt className="total">Usable today</dt><dd className="total">{inr(frozen ? 0 : Math.max(0, balance - heldNow))}</dd>
      </dl>

      {rows.length > 0 && (
        <>
          <h3>{evidenceTitle(p.evidence?.band)}</h3>
          <table className="evtable"><tbody>{rows.map(([k, val]) => <tr key={k}><td>{k}</td><td>{val}</td></tr>)}</tbody></table>
        </>
      )}

      {basis && (
        <details>
          <summary>Legal basis</summary>
          <p style={{ marginTop: 6 }}>{basis}</p>
        </details>
      )}

      {open.map((a) => (
        <div key={a.action_code} style={{ marginBottom: 12 }}>
          {open.length > 1 && <h3>{a.headline}</h3>}
          <Decide proposals={[a]} officer={officer} onDone={onDone} />
        </div>
      ))}
      {open.length > 0 && <p className="note">Your name and the reasons above go into the audit log and the reply to the police.</p>}

      {(p.decisions?.length > 0) && (
        <ul className="history">
          {p.decisions.map((d) => (
            <li key={d.decision_id}>{when(d.decided_at)}: {d.decided_by} {d.decision_outcome.toLowerCase()} {d.action_code === "PROPOSE_NARROW" ? `the smaller hold${d.amount_inr != null ? ` (${inr(d.amount_inr)})` : ""}` : d.action_code === "PROPOSE_FULL_FREEZE" ? "the freeze" : d.action_code === "SEND_KYC" ? "the KYC packet" : "the hold"}{d.reason_text ? `: "${d.reason_text}"` : ""}</li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function AgentLog({ caseId, v }) {
  const { data, error, loading, reload } = useLoad(() => api.log(caseId), [caseId, v]);
  if (loading && !data) return <Loading what="Loading the agent log" />;
  if (error) return <Failed error={error} retry={reload} />;
  return data.map((run) => (
    <section key={run.run_id} style={{ marginBottom: 16 }}>
      {run.summary_text && <p style={{ marginBottom: 10 }}>{run.summary_text}</p>}
      <p className="note" style={{ marginBottom: 8 }}>
        Run {run.run_id}: {run.run_mode === "llm" ? `a local model (${run.model_name}) chose the order, numbers checked against the database` : "fixed order"}, {Number(run.wall_seconds || 0).toFixed(1)} s on Exasol. Purple steps change something; the checker always runs before them.
      </p>
      <ol className="steps">
        {run.steps.map((s) => (
          <li key={s.step_no} className={s.chosen_by === "runtime" ? "runtime" : s.can_change ? "change" : ""}>
            <span className="muted">{s.step_no}</span>
            <span className="tool">{s.tool_name.replaceAll("_", " ")}</span>
            <span>{s.result_text}</span>
            <span className="meta2"><Stmt ids={s.stmt_ids} /> {s.wall_ms} ms</span>
          </li>
        ))}
      </ol>
    </section>
  ));
}

function Paperwork({ caseId, officer, v, onDone }) {
  const { data, error, loading, reload } = useLoad(() => api.paperwork(caseId), [caseId, v]);
  const [err, setErr] = useState(null);
  if (loading && !data) return <Loading what="Loading the paperwork" />;
  if (error) return <Failed error={error} retry={reload} />;
  const send = async (id) => {
    setErr(null);
    if (!officer.trim()) return setErr("Type your name in the Officer box at the top first.");
    try { await api.sendKyc(caseId, id, { officer }); onDone(); } catch (e) { setErr(e.message); }
  };
  const kinds = [["ONWARD", "Alerts to other banks (each opens a ticket there)"], ["EXIT", "Money that left banking (reported to the police)"], ["KYC", "KYC packets (due within a week)"]];
  if (!data.length) return <p className="empty">No paperwork on this ticket.</p>;
  return (
    <>
      {err && <p className="error" role="alert">{err}</p>}
      {kinds.map(([kind, title]) => {
        const list = data.filter((e) => e.entry_kind === kind);
        if (!list.length) return null;
        return (
          <section key={kind} style={{ marginBottom: 16 }}>
            <h3 style={{ marginBottom: 8 }}>{title}</h3>
            <div className="table-wrap">
              <table className="table compact">
                <tbody>
                  {list.map((e) => (
                    <tr key={e.entry_id}>
                      {kind === "KYC" ? (
                        <td><b>{e.account_id}</b><div className="note" style={{ whiteSpace: "pre-wrap" }}>{e.detail}</div></td>
                      ) : (
                        <td><b>{kind === "ONWARD" ? `${e.bank_name}, ${e.account_id}` : e.detail}</b><div className="note"><span className="mono">{e.utr}</span> from {e.from_account} at {clock(e.moved_at)}: {inr(e.amount_inr)}, {inr(e.victim_amount_inr)} of it the victim's</div></td>
                      )}
                      <td className="act">{e.entry_status === "FILED" ? <span className="note">Filed {clock(e.filed_at)} by {e.filed_by}</span> : <button className="btn btn-sm" onClick={() => send(e.entry_id)}>Send to CFCFRMS</button>} <Stmt ids={e.stmt_id} /></td>
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