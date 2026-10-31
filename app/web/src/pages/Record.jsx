import { useState } from "react";
import { api } from "../api.js";
import DecisionCard, { Decided } from "../components/DecisionCard.jsx";
import { Failed, Loading } from "../components/bits.jsx";
import { accountName, clock, inrR, inrSigned, joinAnd, plural, rs, shortName, victim, when } from "../format.js";
import { useLoad } from "../hooks.js";

// The hold record for one account (design board "Hold record: C"): how the held amount was worked out, row by row
// with a running balance that proves it adds up; the history of the hold; what the holder was told; the decision
// waiting, if any; and the accounts it is linked to.
const n = (v) => Number(v || 0);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const date = (d) => (d ? `${Number(String(d).slice(8, 10))} ${MONTHS[Number(String(d).slice(5, 7)) - 1]} ${String(d).slice(0, 4)}` : "");

// The complaints behind this account, as ticket chips in time order: first the ones money is held for here (with
// the amount), then the rest, five at a time behind "Show more".
const TICKET = (
  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" style={{ flex: "none" }}>
    <path d="M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v2.5a2.5 2.5 0 0 0 0 5V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2.5a2.5 2.5 0 0 0 0-5z" />
    <path d="M14 5v2M14 11v2M14 17v2" />
  </svg>
);
function ComplaintChips({ cases, liens, perVictim }) {
  const [all, setAll] = useState(false);
  const sorted = [...cases].sort((x, y) => String(x.notice.reported_at).localeCompare(String(y.notice.reported_at)));
  const tagHere = (c) => liens.find((h) => h.case_id === c.case_id && h.hold_tag !== "NOT_LINKED")?.hold_tag;
  const held = sorted.filter((c) => tagHere(c)), rest = sorted.filter((c) => !tagHere(c));
  const shown = all ? rest : rest.slice(0, 5);
  const notice = (c) => c.notice.ordering_reference ? <span className="amt">police notice {c.notice.ordering_reference}</span> : null;
  if (!held.length && !rest.length) return null;
  return (
    <div className="card" style={{ flex: "1 1 560px", minWidth: 0, padding: "12px 16px" }}>
      {held.length > 0 && <>
        <p className="note" style={{ margin: "0 0 6px" }}>Money held here for {plural(held.length, "complaint")}</p>
        <div className="tchips">{held.map((c) => (
          <a key={c.case_id} className="tchip m" href={`#/case/${encodeURIComponent(c.case_id)}`}>{TICKET}{victim(tagHere(c))} · {clock(c.notice.reported_at)}
            <span className="amt">{inrR(perVictim[tagHere(c)])} held</span>{notice(c)}</a>
        ))}</div>
      </>}
      {rest.length > 0 && <>
        <p className="note" style={{ margin: held.length ? "12px 0 6px" : "0 0 6px" }}>{held.length ? `${plural(rest.length, "other complaint")}` : plural(rest.length, "complaint")}, nothing held for them here</p>
        <div className="tchips">{shown.map((c) => (
          <a key={c.case_id} className="tchip" href={`#/case/${encodeURIComponent(c.case_id)}`}>{TICKET}{c.label}{notice(c)}</a>
        ))}
          {rest.length > 5 && <button className="linkbtn" type="button" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show ${rest.length - 5} more`}</button>}
        </div>
      </>}
    </div>
  );
}

export default function Record({ accountId, officer, onChange }) {
  const { data: r, error, loading, reload } = useLoad(() => api.record(accountId), [accountId]);
  const watch = useLoad(() => api.watch().catch(() => ({ events: [] })), [accountId]);
  if (loading && !r) return <div className="wrap"><Loading what="Loading the hold record" /></div>;
  if (error) return <div className="wrap"><Failed error={error} retry={reload} /></div>;
  const a = r.account;
  const who = shortName(a.holder_name || accountId);
  const active = r.holds.filter((h) => h.hold_status === "ACTIVE");
  const liens = active.filter((h) => h.hold_kind === "LIEN");
  const freeze = active.find((h) => h.hold_kind === "ACCOUNT_FREEZE");
  const held = Math.max(liens.reduce((t, h) => t + n(h.amount_inr), 0), n(freeze?.amount_inr));
  const perVictim = {};
  for (const h of liens) perVictim[h.hold_tag] = (perVictim[h.hold_tag] || 0) + n(h.amount_inr);
  const waiting = (r.proposals_now || []).filter((p) => p.proposal_status === "WAITING");
  const decided = (r.proposals_now || []).filter((p) => p.proposal_status !== "WAITING");
  const balance = r.balance_now_inr ?? r.cases[0]?.libr_steps?.at(-1)?.balance_inr;
  const tagOf = (caseId) => r.holds.find((h) => h.case_id === caseId && h.hold_tag !== "NOT_LINKED")?.hold_tag;
  const reloadAll = () => { reload(); watch.reload(); onChange?.(); };
  const kindWord = { PROPOSE_NARROW: "Smaller hold", PROPOSE_FULL_FREEZE: "Freeze", UNMATCHED_NOTICE: "Near match", PROPOSE_RELEASE: "Release", PROPOSE_PRE_ALERT: "Pre-alert" };
  return (
    <main id="main" className="wrap" style={{ paddingTop: 18, paddingBottom: 40 }}>
      <nav className="note" aria-label="Breadcrumb" style={{ marginBottom: 8 }}><a href="#/holds">Holds</a> / {who}</nav>
      <div style={{ marginBottom: 18 }}>
        <div>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 12px" }}>
            <h1 style={{ fontSize: 30, lineHeight: 1.2 }}>{who} · {inrR(held)} {freeze ? "frozen" : "held"}</h1>
            {freeze ? <span className="pill p-frz">Frozen</span> : liens.length ? <span className="pill p-held">Held</span> : <span className="pill p-plain">Nothing held</span>}
            {waiting.map((p) => <span key={p.proposal_id} className="pill p-wait">{kindWord[p.action_code]} waiting for you</span>)}
          </div>
          <p style={{ fontSize: 14, color: "#44505e", marginTop: 6 }}>{a.account_type ? `${a.account_type[0].toUpperCase()}${a.account_type.slice(1)} account` : "Account"} at {a.bank_name || "Bank X"}{a.opened_on ? `, opened ${date(a.opened_on)}` : ""} · <span className="id">{a.account_id}</span>{active[0] && <> · <span className="id">hold PH-H{active[0].hold_id}</span></>}</p>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-start", gap: "16px 24px", marginTop: 12 }}>
        <ComplaintChips cases={r.cases} liens={liens} perVictim={perVictim} />
        <div className="card" style={{ flex: "0 0 auto", marginLeft: "auto", padding: "14px 20px", display: "flex", flexWrap: "wrap", gap: "8px 32px" }}>
          <div><p className="label">Balance</p><p style={{ fontSize: 22, fontWeight: 700 }}>{inrR(balance)}</p></div>
          {Object.entries(perVictim).map(([tag, amt]) => (
            <div key={tag}><p className="label">Held, {tag === "NOT_LINKED" ? "not linked yet" : victim(tag) + "'s"}</p><p className="v" style={{ fontSize: 22, fontWeight: 700 }}>{inrR(amt)}</p></div>
          ))}
          {freeze && <div><p className="label">Frozen</p><p style={{ fontSize: 22, fontWeight: 700 }}>{inrR(freeze.amount_inr)}</p></div>}
          <div><p className="label">{who} can use</p><p style={{ fontSize: 22, fontWeight: 700 }}>{inrR(freeze ? 0 : Math.max(0, n(balance) - held))}</p></div>
        </div>
        </div>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "flex-start" }}>
        <div style={{ flex: "1 1 620px", minWidth: 0, display: "grid", gap: 22 }}>
          {r.cases.filter((c) => c.libr_steps?.length).map((c) => <WorkedOut key={c.case_id} c={c} r={r} who={who} v={victim(tagOf(c.case_id))} />)}
          <History r={r} who={who} />
          {r.holder_messages.length > 0 && (
            <section className="card" style={{ padding: "20px 22px" }} aria-labelledby="told-h">
              <h2 id="told-h" style={{ fontSize: 19, marginBottom: 12 }}>What {who} was told</h2>
              <div style={{ background: "#f7f9fa", borderRadius: 8, padding: "14px 16px", fontSize: 15, lineHeight: 1.6 }}>{rs(r.holder_messages.at(-1).message_text)}</div>
              <p className="note" style={{ marginTop: 10 }}>Sent by SMS and email at {clock(r.holder_messages.at(-1).written_at)}. The rules require telling the holder the amount, the reason and how to contest it.</p>
            </section>
          )}
        </div>
        <aside style={{ flex: "0 1 360px", minWidth: 0, display: "grid", gap: 14 }}>
          {waiting.map((p) => <DecisionCard key={p.proposal_id} p={p} officer={officer} variant="panel" watch={watch.data?.events || []} position={kindWord[p.action_code]} onDone={reloadAll} />)}
          {!waiting.length && decided.map((p) => <Decided key={p.proposal_id} p={p} officer={officer} onDone={reloadAll} />)}
          {r.linked?.length > 0 && (
            <section className="card" style={{ padding: "16px 16px" }}>
              <h2 style={{ fontSize: 15, marginBottom: 8 }}>Linked accounts</h2>
              {r.linked.map((x) => (
                <div key={x.account_id} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 14, padding: "3px 0" }}>
                  <a href={`#/record/${encodeURIComponent(x.account_id)}`}>{shortName(x.name)}, {x.relation === "received" ? `received ${inrR(x.amount_inr)}` : x.relation === "tried to pay" ? `${who} tried to pay` : x.relation}</a>
                  <span>{n(x.frozen_inr) ? `Frozen ${inrR(x.frozen_inr)}` : n(x.held_inr) ? `${inrR(x.held_inr)} held` : "Nothing held"}</span>
                </div>
              ))}
            </section>
          )}
        </aside>
      </div>
    </main>
  );
}

// How the held amount was worked out: lowest intermediate balance, own money first.
function WorkedOut({ c, r, who, v }) {
  const steps = c.libr_steps;
  const name = (id) => {
    if (!id) return "";
    if (id.startsWith("EXIT:")) return id.replace(/^EXIT:ATM:/, "ATM ").replace(/^EXIT:/, "");
    return shortName(r.names?.[id] || accountName(id));
  };
  const her = v || "the victim";
  const isVictimAcct = (id) => /\(victim/.test(r.names?.[id] || "") || (c.path_into_account?.[0]?.from_account === id && c.path_into_account?.[0]?.hop === 1);
  let prevVictim = 0, prevOwn = 0;
  const rows = steps.map((s, i) => {
    const victimNow = n(s.victim_inr), bal = n(s.balance_inr), own = Math.max(0, bal - victimNow);
    let what;
    if (i === 0 && s.amount_inr == null) what = "Opening balance";
    else if (s.incoming) what = n(s.victim_in_inr) > 0 ? (isVictimAcct(s.counterparty) ? `From ${her}` : `From ${name(s.counterparty)}, ${her === "the victim" ? "the victim's" : "her"} money`) : `From ${name(s.counterparty)}, ${who}'s own`;
    else {
      const fromVictim = Math.max(0, prevVictim - victimNow);
      const fromOwn = n(s.amount_inr) - fromVictim;
      const dest = s.counterparty?.startsWith("EXIT:") ? `Cash at ${name(s.counterparty)}` : `To ${name(s.counterparty)}`;
      what = fromVictim > 0 && fromOwn > 0 ? `${dest}. ${who}'s own ${inrR(fromOwn)} leaves first, then ${inrR(fromVictim)} of ${her === "the victim" ? "the victim's" : "hers"}` : dest;
    }
    const row = { at: i === 0 && s.amount_inr == null ? "Before" : clock(s.at), what, amount: s.amount_inr == null ? null : (s.incoming ? 1 : -1) * n(s.amount_inr), bal, own, victim: victimNow, ref: s.txn_id };
    prevVictim = victimNow; prevOwn = own;
    return row;
  });
  void prevOwn;
  const last = rows.at(-1);
  const inn = steps.filter((s) => s.incoming && n(s.victim_in_inr) > 0).reduce((t, s) => t + n(s.victim_in_inr), 0);
  const outs = {};
  steps.filter((s) => !s.incoming && n(s.victim_in_inr) < 0).forEach((s) => { outs[s.counterparty] = (outs[s.counterparty] || 0) - n(s.victim_in_inr); });
  const firstHold = r.holds.find((h) => h.case_id === c.case_id);
  return (
    <section className="card" style={{ padding: "20px 22px" }} aria-labelledby={`wo-${c.case_id}`}>
      <h2 id={`wo-${c.case_id}`} style={{ fontSize: 19 }}>How {inrR(last?.victim)} {r.cases.length > 1 ? `of ${her === "the victim" ? "the victim's" : `${her}'s`} money ` : ""}was worked out</h2>
      <p style={{ color: "#44505e", marginTop: 2, marginBottom: 12 }}>{who} spends its own money first. {her === "the victim" ? "The victim's" : `${her}'s`} share can only go down after {her === "the victim" ? "the" : "her"} money arrives, never up.</p>
      <div className="tw">
        <table className="t">
          <thead><tr><th scope="col">Time</th><th scope="col">What happened</th><th scope="col" className="r">Amount</th><th scope="col" className="r">Balance after</th><th scope="col" className="r">{who}'s own</th><th scope="col" className="r">{her === "the victim" ? "Victim's" : `${her}'s`}</th><th scope="col">Source</th></tr></thead>
          <tbody>
            {rows.map((x, i) => (
              <tr key={i}><td className="mono" style={x.at === "Before" ? { fontFamily: "inherit", color: "#5b6675" } : undefined}>{x.at}</td><td>{x.what}</td>
                <td className="r">{x.amount == null ? "" : inrSigned(x.amount)}</td><td className="r">{inrR(x.bal)}</td><td className="r own">{inrR(x.own)}</td><td className="r vic">{inrR(x.victim)}</td>
                <td>{x.ref ? <span className="stmt">txn {x.ref}</span> : ""}</td></tr>
            ))}
            {firstHold && <tr style={{ background: "#f7f9fa" }}><td className="mono">{clock(firstHold.placed_at)}</td><td><b>Held: {her === "the victim" ? "the victim's" : "her"} lowest share</b></td><td /><td className="r"><b>{inrR(last?.bal)}</b></td><td className="r own">{inrR(last?.own)}</td><td className="r vic">{inrR(last?.victim)}</td><td>{firstHold.stmt_id ? <span className="stmt">stmt {firstHold.stmt_id}</span> : ""}</td></tr>}
          </tbody>
        </table>
      </div>
      {inn > 0 && <p style={{ marginTop: 10 }}><b>It adds up:</b> {inrR(inn)} in = {inrR(last?.victim)} still in {who}{Object.entries(outs).map(([to, amt]) => ` + ${inrR(amt)} ${to.startsWith("EXIT:") ? "taken as cash" : `sent to ${name(to)}`}`).join("")}.</p>}
    </section>
  );
}

function History({ r, who }) {
  const items = [];
  const byId = Object.fromEntries(r.holds.map((h) => [h.hold_id, h]));
  for (const h of r.holds) {
    const amt = inrR(h.amount_inr);
    if (h.hold_kind === "MARKER") items.push([h.placed_at, "#5b6675", <><b>Marked to catch new money</b> by the system. Nothing was left to hold.</>, `stmt ${h.stmt_id}`]);
    else if (h.placed_by === "agent") items.push([h.placed_at, "#5b3fa0", <><b>{h.hold_tag === "NOT_LINKED" ? "New money held by the system" : h.action_code === "LINKED_HOLD" ? "Linked to a new complaint by the system" : "Hold placed by the system"}</b>, {amt}.{h.action_code === "MANDATORY_HOLD" ? " All checks passed." : ""}</>, `stmt ${h.stmt_id}`]);
    else if (h.hold_kind === "ACCOUNT_FREEZE") items.push([h.placed_at, "#17212b", <><b>Account frozen by {h.placed_by}</b>, {amt}. {rs(h.reason_text || "")}</>, `stmt ${h.stmt_id}`]);
    else items.push([h.placed_at, "#17212b", <><b>{h.action_code === "HOLD_AGAIN" ? `Held again by ${h.placed_by}` : `Hold changed to ${amt} by ${h.placed_by}`}</b>{h.action_code === "HOLD_AGAIN" ? `, ${amt}` : ""}. {rs(h.reason_text || "")}</>, `stmt ${h.stmt_id}`]);
    if (h.hold_status === "RELEASED") items.push([h.placed_at, "#5b6675", <><b>Hold released</b>, {amt}.</>, null]);
    void byId;
  }
  for (const m of r.holder_messages) items.push([m.written_at, "#8a95a3", <><b>Holder told</b> by SMS and email. Text below.</>, null]);
  for (const e of r.watch_events) items.push([e.event_at, "#2f7a5a", <b>{rs(e.sentence || "").replace(/^\d\d:\d\d: /, "")}</b>, `watch ${e.stmt_id ?? ""}`]);
  for (const c of r.cases) {
    for (const p of c.proposals.filter((p) => !["MANDATORY_HOLD", "HOLD_MARKER", "RECORD_EXIT", "ONWARD_ENTRY", "KYC_PACKET", "LINK_DUPLICATE", "LINKED_HOLD", "WATCH_ADD", "RECORD_SPLIT", "SEND_BACK", "WATCH_HOLD"].includes(p.action_code)))
      items.push([p.proposed_at, "#b07a1c", <><b>{{ PROPOSE_NARROW: "Smaller hold suggested", PROPOSE_FULL_FREEZE: "Freeze suggested", UNMATCHED_NOTICE: "Near match suggested", PROPOSE_RELEASE: "Release suggested", PROPOSE_PRE_ALERT: "Pre-alert prepared" }[p.action_code] || "Suggested"} by the system</b>{p.proposed_by === "watch" ? (p.action_code === "PROPOSE_PRE_ALERT" ? ", after a payment from a new payer" : ", after an attempt to move held money") : ""}. {p.proposal_status === "WAITING" ? "Waiting for a nodal officer." : ""}</>, `proposal ${p.proposal_id}`]);
    for (const d of c.decisions) items.push([d.decided_at, "#17212b", <><b>{d.decision_outcome === "REJECTED" ? (d.action_code === "PROPOSE_PRE_ALERT" ? `Pre-alert not sent, by ${d.decided_by}` : `Kept as it was by ${d.decided_by}`) : d.action_code === "PROPOSE_PRE_ALERT" ? `Pre-alert sent by ${d.decided_by}` : { HOLD_AGAIN: "Held again", UNFREEZE: "Unfrozen", RELEASE: "Released" }[d.action_code] ? `${{ HOLD_AGAIN: "Held again", UNFREEZE: "Unfrozen", RELEASE: "Released" }[d.action_code]} by ${d.decided_by}` : `Approved by ${d.decided_by}`}</b>{d.reason_text ? `: "${rs(d.reason_text)}"` : "."}</>, `decision D-${String(d.decision_id).padStart(2, "0")}`]);
  }
  items.sort((x, y) => String(x[0]).localeCompare(String(y[0])));
  return (
    <section className="card" style={{ padding: "20px 22px" }} aria-labelledby="hist-h">
      <h2 id="hist-h" style={{ fontSize: 19, marginBottom: 6 }}>History</h2>
      <ol className="hist">
        {items.map(([t, color, text, ref], i) => (
          <li key={i}><span className="mono">{clock(t)}</span><span className="hdot" style={{ background: color }} aria-hidden="true" /><span>{text} {ref && <span className="stmt">{ref}</span>}</span></li>
        ))}
      </ol>
      {!items.length && <p className="note">Nothing has happened on {who}'s hold yet.</p>}
      <p className="note" style={{ marginTop: 8 }}>Every change keeps who made it and why. {when(items.at(-1)?.[0]) !== "–" ? `Last change ${when(items.at(-1)?.[0])}.` : ""}</p>
    </section>
  );
}

export { joinAnd };