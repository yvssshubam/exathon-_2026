import { useState } from "react";
import { api } from "../api.js";
import Decide from "../components/Decide.jsx";
import { SinceHold } from "../components/Watch.jsx";
import { Failed, Loading, Stmt } from "../components/bits.jsx";
import { evidenceRows, evidenceTitle } from "../evidence.js";
import { age, clock, inr, plural, when } from "../format.js";
import { useLoad } from "../hooks.js";

const MATCH = {
  EXACT: "matches the payment", ACCEPTED: "near match accepted by the officer", NEAR_MATCH: "near match only: nothing held",
  NO_MATCH: "no such payment at this bank: nothing held", INCOMPLETE: "incomplete: nothing held",
};
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
      <nav className="crumb" aria-label="Breadcrumb"><a href="#/review">Review</a> / <a href="#/queue">Complaints</a> / {c.label || `${clock(c.ticket?.received_at || c.reported_at)} complaint`}</nav>
      <div className="pagehead">
        <div>
          <h1>{inr(c.amount_inr)} reported at {clock(c.ticket?.received_at || c.reported_at)}</h1>
          <div className="meta">
            {c.txn_at && <span>Paid {clock(c.txn_at)}{c.paid_channel ? ` by ${c.paid_channel}` : ""}</span>}
            <span className="mono">UTR {c.utr}</span>
            {c.ordering_authority && <span>{c.ordering_authority}{c.ordering_reference ? ` · ${c.ordering_reference}` : ""}</span>}
            <span>{c.notice_legal_basis ? `Notice under ${c.notice_legal_basis}` : "No legal basis given on the notice"}</span>
            {c.intake && <span>Intake: {MATCH[c.intake.match_status] || c.intake.match_status}{c.intake.related && c.intake.related !== "NEW" ? `; ${c.intake.related.toLowerCase().replace("_", " ")} of ${c.intake.related_case_id}` : ""}</span>}
            {c.ticket_kind === "ONWARD" && <span>Onward entry from {String(c.from_bank_id || "").replace("BANK_", "Bank ")} ({c.parent_case_id})</span>}
          </div>
        </div>
        <div className="row">
          <CaseStatus c={c} run={run} onRetry={workNow} busy={busy} />
          {traced && <a className="btn btn-sm" href={`#/compare/${caseId}`}>Manual vs system</a>}
        </div>
      </div>

      {!worked && (
        <div className="card pad" style={{ marginBottom: 24 }}>
          <p style={{ marginBottom: 10 }}>The system has not handled this complaint yet.</p>
          <button className="btn btn-primary" disabled={busy} onClick={workNow}>{busy ? "The system is working on it…" : "Handle it now"}</button>
          {err && <p className="error" role="alert">{err}</p>}
        </div>
      )}
      {c.intake && !["EXACT", "ACCEPTED"].includes(c.intake.match_status) && (
        <p className="status" role="status" style={{ whiteSpace: "pre-wrap" }}>{c.intake.related === "DUPLICATE" ? "" : `Sent back to the police at once. ${c.intake.mismatched || c.intake.problems || ""}`}</p>
      )}
      {c.links?.length > 0 && <p className="note">{c.links.map((l) => `${l.link_kind.toLowerCase().replace("_", " ")}: ${l.case_id} ↔ ${l.linked_case_id}${l.detail ? ` (${l.detail})` : ""}`).join(" · ")}</p>}
      {err && <p className="error" role="alert">{err}</p>}

      {traced && <Worked caseId={caseId} c={c} accountId={accountId} officer={officer} v={v} bump={bump} />}
      {!traced && (c.proposals || []).filter((x) => x.proposal_status === "WAITING").map((x) => (
        <div key={x.proposal_id} className="card pad" style={{ marginBottom: 16 }}>
          <h3>{x.headline}</h3>
          <ul className="why">{x.reasons_for.map((r) => <li key={r}>{r.replace(/Rs (?=\d)/g, "₹")}</li>)}</ul>
          {x.case_against.length > 0 && <><h5 className="why-h against">The case against</h5><ul className="why against">{x.case_against.map((r) => <li key={r}>{r}</li>)}</ul></>}
          <Decide p={x} officer={officer} onDone={bump} />
        </div>
      ))}

      {worked && (
        <div className="more">
          <details id="steplog">
            <summary>What the system did, step by step</summary>
            <AgentLog caseId={caseId} v={v} />
          </details>
          {(
            <details>
              <summary>Filed with the portal</summary>
              <Paperwork caseId={caseId} v={v} />
            </details>
          )}
        </div>
      )}
    </main>
  );
}

const STEP_ORDER = ["PROPOSE_NARROW", "PROPOSE_FULL_FREEZE", "UNMATCHED_NOTICE", "PROPOSE_RELEASE"];
const DONE_WORDS = {
  PROPOSE_NARROW: (p) => `hold reduced to ${inr(p.decided_amount_inr ?? p.amount_inr)}`, PROPOSE_FULL_FREEZE: () => "full account frozen",
  UNMATCHED_NOTICE: () => "near match accepted", PROPOSE_RELEASE: () => "hold released",
};

// The status label beside the title: the first thing the officer reads, so each case has its exact words.
function CaseStatus({ c, run, onRetry, busy }) {
  if (!run) return null;
  const decided = (c.proposals || []).filter((p) => p.decided_at).sort((a, b) => String(b.decided_at).localeCompare(String(a.decided_at)));
  const waiting = (c.proposals || []).filter((p) => p.proposal_status === "WAITING");
  if (["FAILED", "CHECK_FAILED"].includes(run.run_status)) {
    return (
      <span className="statusline problem">
        <span className="pill pill-alert">Could not complete: {c.problem}</span>
        <button className="btn btn-sm" disabled={busy} onClick={onRetry}>{busy ? "Trying…" : "Try again"}</button>
        <a className="btn btn-sm btn-quiet" href="#steplog" onClick={() => { const d = document.getElementById("steplog"); if (d) d.open = true; }}>Handle manually</a>
      </span>
    );
  }
  let text;
  if (run.run_status === "SENT_BACK") text = "Sent back to the police: the details do not match a payment";
  else if (run.run_status === "LINKED") text = "Linked to an earlier complaint: nothing held twice";
  else if (!waiting.length && decided.length) text = `Completed by ${decided[0].decided_by}, ${clock(decided[0].decided_at)}`;
  else if (!run.sim_holds_at) text = `Nothing to hold: money left before ${clock(c.ticket?.received_at || c.reported_at)}`;
  else {
    const mins = Math.round((new Date(String(run.sim_holds_at).replace(" ", "T")) - new Date(String(c.ticket?.received_at || c.reported_at).replace(" ", "T"))) / 60000);
    text = `Money held at ${clock(run.sim_holds_at)}, ${c.after_hours ? "before office hours" : `${plural(mins, "min", "min")} after the complaint`}`;
  }
  return <span className="pill pill-night">{text}</span>;
}

function Worked({ caseId, c, accountId, officer, v, bump }) {
  const [just, setJust] = useState(null);       // { id, officer } of the decision just taken here
  const trail = useLoad(() => api.trail(caseId), [caseId, v]);
  const acts = useLoad(() => api.actions(caseId), [caseId, v]);
  if ((trail.loading && !trail.data) || (acts.loading && !acts.data)) return <Loading what="Drawing the money trail" />;
  if (trail.error) return <Failed error={trail.error} retry={trail.reload} />;
  if (acts.error) return <Failed error={acts.error} retry={acts.reload} />;
  const { nodes, edges } = trail.data;
  const actions = acts.data.map((a) => ({ ...a, case_id: caseId }));
  const waiting = (c.proposals || []).filter((p) => p.proposal_status === "WAITING")
    .sort((a, b) => STEP_ORDER.indexOf(a.action_code) - STEP_ORDER.indexOf(b.action_code) || a.proposal_id - b.proposal_id);
  const own = nodes.filter((n) => n.kind === "own");
  const selected = accountId && nodes.some((n) => n.id === accountId && n.kind !== "exit") ? accountId
    : waiting[0]?.account_id || own[0]?.id || null;
  const justP = just && (c.proposals || []).find((p) => p.proposal_id === just.id);
  const next = waiting.find((p) => p.account_id !== justP?.account_id) || waiting[0];
  const kyc = Number(c.filed?.KYC || 0);
  return (
    <>
      <CaseStats actions={actions} edges={edges} run={c.run} waiting={waiting} />
      <div className="casegrid">
        <TrailCards c={c} nodes={nodes} edges={edges} actions={actions} selected={selected} caseId={caseId} />
        <div className="asidecol">
          {justP && justP.decided_at && (
            <div className="card pad justdone" role="status">
              <p><b>{justP.name.replace(/\s*\(.*\)$/, "")}: {justP.proposal_status === "REJECTED" ? "kept as it was" : (DONE_WORDS[justP.action_code] || (() => "approved"))(justP)}</b></p>
              <p className="note">{justP.proposal_status === "REJECTED" ? "Decided" : "Approved"} by {justP.decided_by} at {clock(justP.decided_at)}. Your reasons are saved in the audit log and the hold record.</p>
              {next && <a className="btn btn-sm" href={`#/case/${caseId}/${encodeURIComponent(next.account_id)}`} onClick={() => setJust(null)}>Next: {next.name.replace(/\s*\(.*\)$/, "")}</a>}
            </div>
          )}
          {waiting.length > 1 && (
            <ol className="steps-top" aria-label="Decisions on this complaint">
              {waiting.map((p, i) => (
                <li key={p.proposal_id} className={p.account_id === selected ? "on" : ""}>
                  <a href={`#/case/${caseId}/${encodeURIComponent(p.account_id)}`}>{i + 1} · {p.name.replace(/\s*\(.*\)$/, "")}</a>
                </li>
              ))}
            </ol>
          )}
          {waiting.length === 0 && (
            <div className="card pad nothingwaiting">
              <p><b>Nothing is waiting for you on this complaint.</b></p>
              <p className="note">Holds and reports were done automatically.{kyc ? ` ${plural(kyc, "KYC packet")} filed with the portal in the same run.` : ""}</p>
            </div>
          )}
          {selected && <AccountAside key={selected + v} caseId={caseId} node={nodes.find((n) => n.id === selected)} edges={edges} officer={officer} v={v}
                                     onDone={(p) => { setJust({ id: p.proposal_id }); bump(); }} />}
        </div>
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
    count("PROPOSE_NARROW") && plural(count("PROPOSE_NARROW"), "smaller hold"),
    count("PROPOSE_FULL_FREEZE") && plural(count("PROPOSE_FULL_FREEZE"), "full-account freeze"),
    count("UNMATCHED_NOTICE") && plural(count("UNMATCHED_NOTICE"), "unmatched notice"),
    count("PROPOSE_RELEASE") && plural(count("PROPOSE_RELEASE"), "release"),
  ].filter(Boolean);
  return (
    <div className="stats">
      <div className={`card pad stat${held ? " held" : " zero"}`}>
        <div className="label">Held at Bank X</div>
        <div className="big">{inr(held)}</div>
        <div className="sub">{holds.length ? `on ${plural(holds.length, "account")}. ${heldVictim === held ? "All of it is the victim's." : `${inr(heldVictim)} of it is the victim's.`}` : "Nothing was left to hold."}</div>
      </div>
      <div className={`card pad stat${onward.length ? " sent" : " zero"}`}>
        <div className="label">Sent to another bank</div>
        <div className="big">{inr(onward.reduce((s, a) => s + (a.amount_inr || 0), 0))}</div>
        <div className="sub">{onward.length ? `to ${joinAnd(banks)}. ${joinAnd(banks)} informed at ${clock(run?.sim_holds_at)}.` : "No money went to another bank."}</div>
      </div>
      <div className={`card pad stat${exitTotal ? "" : " zero"}`}>
        <div className="label">{allCash || !exits.length ? "Withdrawn as cash" : "Left banking"}</div>
        <div className="big">{inr(exitTotal)}</div>
        <div className="sub">{exits.length ? (exits.length === 1 ? `at ${exits[0].exit_identifier}, at ${clock(exits[0].ts)}` : `${plural(exits.length, "withdrawal")}, reported to the police`) : "No cash was withdrawn."}</div>
      </div>
      <div className={`pad stat waitcard${waiting.length ? "" : " clear"}`}>
        <div className="label">Needs your approval</div>
        <div className="big">{waiting.length}</div>
        <div className="sub">{waitParts.length ? waitParts.join(", ") : "Nothing left to approve."}</div>
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
      <p>Read from left to right. Each card shows what the account received, from whom, and what the system did.</p>
      <div className="hops">
        {Object.keys(hops).sort((a, b) => a - b).map((h) => (
          <div className="hop" key={h}>
            <div className="hop-h">Layer {h}</div>
            <Layer list={hops[h]} edges={edges} selected={selected} render={(n) => <HopCard key={n.id} n={n} edges={edges} actions={actions.filter((a) => a.account_id === n.id)} proposals={(c.proposals || []).filter((p) => p.account_id === n.id)} label={label} on={n.id === selected} caseId={caseId} />} />
          </div>
        ))}
      </div>
      <p className="note" style={{ marginTop: 14 }}>The victim's share is calculated by assuming each account spends its own money first and the victim's money last.</p>
    </section>
  );
}

// A layer shows its 3 largest accounts, then one button for the rest with their total. The selected account and any
// account waiting for a decision always stay visible.
function Layer({ list, edges, selected, render }) {
  const [all, setAll] = useState(false);
  const amount = (n) => edges.filter((e) => e.to_account === n.id).reduce((s, e) => s + Number(e.amount_inr || 0), 0);
  const sorted = [...list].sort((a, b) => amount(b) - amount(a));
  const keep = all ? sorted : sorted.filter((n, i) => i < 3 || n.id === selected || (n.waiting || []).length);
  const rest = sorted.filter((n) => !keep.includes(n));
  return (
    <>
      {keep.map(render)}
      {rest.length > 0 && <button className="btn btn-sm morecards" onClick={() => setAll(true)}>{plural(rest.length, "more account")} · {inr(rest.reduce((s, n) => s + amount(n), 0))}</button>}
    </>
  );
}

function HopCard({ n, edges, actions, proposals, label, on, caseId }) {
  const ins = edges.filter((e) => e.to_account === n.id);
  const outs = edges.filter((e) => e.from_account === n.id);
  const got = ins.length ? `Received ${inr(ins[0].amount_inr)} from ${label[ins[0].from_account]}, ${clock(ins[0].ts)}${ins.length > 1 ? `, and ${plural(ins.length - 1, "more payment")}` : ""}` : "";
  if (n.kind === "exit") {
    const isAtm = n.id.startsWith("EXIT:ATM");
    return (
      <div className="hopcard gone">
        <div className="top"><b>{isAtm ? `Cash, ${n.label.replace(/^ATM\s*/, "ATM ")}` : n.label}</b><span className="pill pill-gone">Withdrawn</span></div>
        <div className="got">{got}. Reported to the police as {isAtm ? "cashed out" : "moved to crypto"}.</div>
      </div>
    );
  }
  const hold = actions.find((a) => a.action_code === "MANDATORY_HOLD");
  const marker = actions.find((a) => a.action_code === "HOLD_MARKER");
  const open = proposals.filter((p) => p.proposal_status === "WAITING");
  const decided = proposals.filter((p) => ["APPROVED", "EDITED", "REJECTED"].includes(p.proposal_status));
  const honest = n.evidence === "HONEST" || open.some((a) => a.action_code === "PROPOSE_NARROW");
  let pill;
  if (n.kind === "other") pill = <span className="pill pill-other">Other bank</span>;
  else if (honest) pill = <span className="pill pill-honest">Genuine customer</span>;
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
  const nextText = open.map((a) => (a.action_code === "PROPOSE_FULL_FREEZE" ? "Full-account freeze suggested" : a.action_code === "PROPOSE_NARROW" ? "Smaller hold suggested" : a.action_code === "PROPOSE_RELEASE" ? "Release suggested" : "Decision waiting")).join(" · ");
  const doneText = decided.map((a) => `${a.proposal_status === "REJECTED" ? "Kept as it was" : a.action_code === "PROPOSE_FULL_FREEZE" ? "Frozen" : a.action_code === "PROPOSE_NARROW" ? "Hold reduced" : "Decided"}`).join(" · ");
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
  if (p.action_code === "PROPOSE_NARROW") return `Reduce ${name}'s hold to ${inr(p.amount_inr)}`;
  if (p.action_code === "PROPOSE_FULL_FREEZE") return Number(p.amount_inr) ? `Freeze the full account of ${name}?` : `Hold money that comes in later on ${name}?`;
  if (p.action_code === "UNMATCHED_NOTICE") return `Accept the near match on ${name}'s account?`;
  return p.headline || `${p.action_code} on ${name}`;
}

function AccountAside({ caseId, node, edges, officer, v, onDone }) {
  const panel = useLoad(() => api.account(caseId, node.id), [caseId, node.id, v]);
  if (panel.loading && !panel.data) return <aside className="card aside"><Loading what="Loading the account" /></aside>;
  if (panel.error) return <aside className="card aside"><Failed error={panel.error} retry={panel.reload} /></aside>;
  const p = panel.data;
  const acc = p.account || {};
  const name = short(acc.holder_name || node.label);
  const actions = p.actions.map((a) => ({ ...a, case_id: caseId, account_id: node.id }));
  const open = (p.proposals || []).filter((x) => x.proposal_status === "WAITING");
  const auto = actions.find((a) => a.act_mode === "AUTOMATIC");
  const lead = open[0];
  const sub = [acc.holder_name && acc.holder_name !== name ? acc.holder_name : name,
    `${acc.bank_name || node.bank_name || ""} ${acc.account_type || ""} account`.replace(/\s+/g, " ").trim()
      + (acc.opened_on ? `, open ${age(acc.opened_on, p.split?.as_of)}` : "")].join(", ");

  if (node.kind === "other") {
    const moved = edges.filter((e) => e.to_account === node.id);
    return (
      <aside className="card aside" aria-labelledby="acc-h">
        <div className="kicker done">Done by the system</div>
        <h2 id="acc-h">{node.bank_name} was alerted</h2>
        <p className="sub">{sub}.</p>
        <dl className="money">
          {moved.map((e) => [<dt key={e.txn_id + "t"}>Moved {clock(e.ts)}</dt>, <dd key={e.txn_id + "d"}>{inr(e.amount_inr)}</dd>,
            <dt key={e.txn_id + "vt"} className="in victim">of which the victim's</dt>, <dd key={e.txn_id + "vd"} className="victim">{inr(e.victim_inr)}</dd>])}
        </dl>
        <p className="note">The onward entry opens a complaint at {node.bank_name}; their nodal desk places the hold there. Bank X cannot hold money in another bank.</p>
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
  const basis = lead?.citation || auto?.legal_basis;

  return (
    <aside className="card aside" aria-labelledby="acc-h">
      {lead ? <div className="kicker">Needs your approval</div> : <div className="kicker done">{p.decisions?.length ? "Decided" : "Done by the system"}</div>}
      <h2 id="acc-h">{lead ? headlineFor(lead, name) : frozen ? `${name}'s account is frozen` : heldNow ? `${inr(heldNow)} held on ${name}` : `Nothing held on ${name}`}</h2>
      <p className="sub">{sub}.</p>

      {lead?.reasons_for?.length > 0 && (
        <ul className="reasons">{lead.reasons_for.map((s) => <li key={s}>{s.replace(/Rs (?=\d)/g, "₹")}</li>)}</ul>
      )}
      {lead?.case_against?.length > 0 && (
        <>
          <h3>The case against</h3>
          <ul className="reasons against">{lead.case_against.map((s) => <li key={s}>{s.replace(/Rs (?=\d)/g, "₹")}</li>)}</ul>
        </>
      )}

      <h3>The money</h3>
      <dl className="money">
        <dt>Balance</dt><dd>{inr(balance)}</dd>
        <dt>{frozen ? "Frozen" : "Held"}{since ? ` since ${clock(since)}` : ""}</dt><dd>{inr(frozen ? balance : heldNow)}</dd>
        {heldNow > 0 && <><dt className="in victim">of which the victim's</dt><dd className="victim">{inr(victim)}</dd></>}
        {others > 0 && <><dt className="in others">of which other victims'</dt><dd className="others">{inr(others)}</dd></>}
        {heldNow > 0 && ownPart > 0 && <><dt className="in own">of which the customer's own</dt><dd className="own">{inr(ownPart)}</dd></>}
        <dt className="total">Available to the customer</dt><dd className="total">{inr(frozen ? 0 : Math.max(0, balance - heldNow))}</dd>
      </dl>

      {rows.length > 0 && (
        <>
          <h3>{evidenceTitle(p.evidence?.band)}</h3>
          <table className="evtable"><tbody>{rows.map(([k, val]) => <tr key={k}><td>{k}</td><td>{val}</td></tr>)}</tbody></table>
        </>
      )}

      <SinceHold events={p.watch} />

      {basis && (
        <details>
          <summary>Legal basis</summary>
          <p style={{ marginTop: 6 }}>{basis}</p>
        </details>
      )}

      {open.map((a) => (
        <div key={a.proposal_id} style={{ marginBottom: 12 }}>
          {open.length > 1 && <h3>{a.headline}</h3>}
          <Decide p={a} officer={officer} onDone={() => onDone(a)} />
        </div>
      ))}
      {open.length > 0 && <p className="note">Your name and these reasons will be saved in the audit log and in the hold record.</p>}
      <p className="note"><a href={`#/record/${encodeURIComponent(node.id)}`}>Open the hold record</a></p>

      {(p.decisions?.length > 0) && (
        <ul className="history">
          {p.decisions.map((d) => (
            <li key={d.decision_id}>{when(d.decided_at)}: {d.decided_by} {d.decision_outcome.toLowerCase()} {d.action_code === "PROPOSE_NARROW" ? `the smaller hold${d.amount_inr != null ? ` (${inr(d.amount_inr)})` : ""}` : d.action_code === "PROPOSE_FULL_FREEZE" ? "the freeze" : d.action_code === "PROPOSE_RELEASE" ? "the release" : "the near match"}{d.reason_text ? `: "${d.reason_text}"` : ""}</li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function AgentLog({ caseId, v }) {
  const { data, error, loading, reload } = useLoad(() => api.log(caseId), [caseId, v]);
  if (loading && !data) return <Loading what="Loading the log" />;
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

function Paperwork({ caseId, v }) {
  const { data, error, loading, reload } = useLoad(() => api.paperwork(caseId), [caseId, v]);
  if (loading && !data) return <Loading what="Loading the filings" />;
  if (error) return <Failed error={error} retry={reload} />;
  const kinds = [["ONWARD", "Onward entries (each becomes a complaint at the next bank within a minute)"],
    ["EXIT", "Money that left banking (reported for the police)"], ["KYC", "KYC packets (filed in the same run)"],
    ["SEND_BACK", "Sent back to the police"]];
  if (!data.length) return <p className="empty">Nothing filed on this complaint.</p>;
  return kinds.map(([kind, title]) => {
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
                  {kind === "KYC" || kind === "SEND_BACK" ? (
                    <td><b>{e.account_id}</b><div className="note" style={{ whiteSpace: "pre-wrap" }}>{e.detail}</div></td>
                  ) : (
                    <td><b>{kind === "ONWARD" ? `${e.bank_name}, ${e.account_id}` : e.detail}</b><div className="note"><span className="mono">{e.utr}</span> from {e.from_account} at {clock(e.moved_at)}: {inr(e.amount_inr)}, {inr(e.victim_amount_inr)} of it the victim's</div></td>
                  )}
                  <td className="act"><span className="note">Filed {clock(e.filed_at)} by {e.filed_by}</span> <Stmt ids={e.stmt_id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    );
  });
}