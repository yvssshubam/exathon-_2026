import { useState } from "react";
import { api, SNAPSHOT } from "../api.js";
import { accountName, ageDays, ageWords, cap, clock, inrR, rs, shortName, stay, victim as victimName } from "../format.js";
import { useLoad } from "../hooks.js";

// One layout for every decision the officer makes (smaller hold, freeze, near match, release), used on Review,
// on the complaint page and in the hold record. Top to bottom: kind and urgency, the question, whose account, what
// changes, evidence (each row says which way it reads), the case against, since the hold, legal basis, actions.
// variant "wide" is the Review card; "panel" the narrower card beside a complaint or a hold record.

export const KIND = { PROPOSE_NARROW: "Smaller hold", PROPOSE_FULL_FREEZE: "Freeze", UNMATCHED_NOTICE: "Near match · nothing held yet",
  PROPOSE_RELEASE: "Release" };
const UNDO = { PROPOSE_NARROW: ["Hold again", "HOLD_AGAIN"], PROPOSE_FULL_FREEZE: ["Unfreeze", "UNFREEZE"], UNMATCHED_NOTICE: ["Release", "RELEASE"] };
const LEGAL = {
  wide: { PROPOSE_FULL_FREEZE: "SOP 9.1(iii), a full freeze needs repeated fraud credits and an officer's order.",
    UNMATCHED_NOTICE: "S.168 BNSS, a hold needs a notice that names the payment; an officer may accept a near match." },
  panel: { PROPOSE_FULL_FREEZE: "a full freeze needs reasons beyond the traced amount (SOP 9.1(iii)).",
    UNMATCHED_NOTICE: "a hold must rest on a police notice that identifies the payment." },
};
const MOVE_KINDS = ["CASHOUT_BLOCKED", "TRANSFER_BLOCKED"];
const n = (v) => Number(v || 0);
const channelOf = (utr) => (/^(IMPS|UPI|NEFT|RTGS)/.exec(String(utr || "")) || [])[1] || "–";

// ---------------------------------------------------------------- facts every card reads
export function figures(p) {
  const held = n(p.current_hold_inr);
  const target = n(p.amount_inr);
  const balance = n(p.balance_now_inr || p.balance_today_inr);
  return { held, target, balance, release: Math.max(0, held - target), victim: victimName(p.victim_tag) || "the victim" };
}

// What happened on the account after the hold, in one short line.
export function sinceLine(e, names = {}) {
  const amt = inrR(e.amount_inr);
  const to = names[e.counterparty] || (e.counterparty || "").replace(/^EXIT:ATM:/, "ATM ");
  switch (e.event_kind) {
    case "NEW_MONEY_HONEST": return `a customer paid ${amt}. Not held.`;
    case "CASHOUT_BLOCKED": return `tried to withdraw ${amt}${to ? ` at ${to}` : ""}. Refused.`;
    case "TRANSFER_BLOCKED": return `tried to send ${amt}${to ? ` to ${to}` : ""}. Refused.`;
    case "NEW_MONEY_HELD": case "NEW_MONEY_ABOVE_NOTICE": return `${amt} more came in, held.`;
    case "LINKED_TO_VICTIM": return "the money caught was linked to a new complaint.";
    case "RECIPIENT_WATCHED": return "the account it tried to pay is now watched.";
    default: return rs(e.sentence || "").replace(/^\d\d:\d\d: /, "");
  }
}

// The events worth a line after a hold, as a short phrase ("ATM ₹29,500 refused"). Notes about the other side of a
// refused payment (another bank, a business) stay in the hold record and the audit log.
export const KEY_EVENTS = ["CASHOUT_BLOCKED", "TRANSFER_BLOCKED", "NEW_MONEY_HELD", "NEW_MONEY_ABOVE_NOTICE", "NEW_MONEY_HONEST", "MOVED"];
export function shortEvent(e) {
  const amt = inrR(e.amount_inr);
  const cp = String(e.counterparty || "");
  const dest = /Bank [A-Z]/.exec(cp)?.[0] || (cp && !/^EXIT:|wallet|ATM/i.test(cp) ? shortName(cp) : "");
  switch (e.event_kind) {
    case "CASHOUT_BLOCKED": return `${/crypto|wallet/i.test(cp) || e.channel === "CRYPTO" ? "Crypto" : "ATM"} ${amt} refused`;
    case "TRANSFER_BLOCKED": return `${amt}${dest ? ` to ${dest}` : ""} refused`;
    case "NEW_MONEY_HELD": case "NEW_MONEY_ABOVE_NOTICE": return `${amt} came in, held`;
    case "NEW_MONEY_HONEST": return `a customer paid ${amt}, not held`;
    case "MOVED": return `${amt} not held, moved out`;
    default: return sinceLine(e);
  }
}

export function attemptsOf(p, watch) {
  return (watch || []).filter((e) => e.account_id === p.account_id && MOVE_KINDS.includes(e.event_kind));
}

// The clear-case test for approving several smaller holds at once.
export function clearCase(p) {
  const days = ageDays(p.opened_on, p.held_since || p.proposed_at);
  const f = figures(p);
  if (days != null && days < 365) return [false, "Account under 1 year"];
  if (n(p.cash_exits) > 0) return [false, `${Math.round(n(p.cash_exits))} cash withdrawal${Math.round(n(p.cash_exits)) === 1 ? "" : "s"} in 90 days`];
  if (f.balance && f.target / f.balance >= 0.05) return [false, `Victim's share is ${Math.round((f.target / f.balance) * 100)}% of balance`];
  return [true, null];
}

export function balanceChanged(p) {
  return p.balance_today_inr != null && p.balance_now_inr != null && Math.round(n(p.balance_today_inr)) !== Math.round(n(p.balance_now_inr));
}

function unmatchedRows(p, receiver) {
  const sameMin = clock(p.notice_at) === clock(p.near_at);
  const rows = [
    ["Amount", inrR(p.complaint_amount_inr), inrR(p.near_amount_inr), n(p.complaint_amount_inr) === n(p.near_amount_inr)],
    ...(receiver ? [["Receiver", shortName(p.name), shortName(p.name), true]] : []),
    ["Paid at", clock(p.notice_at), clock(p.near_at), sameMin, sameMin ? "Same minute" : null],
    ["Channel", channelOf(p.notice_utr), channelOf(p.near_utr), channelOf(p.notice_utr) === channelOf(p.near_utr)],
    ["UTR", p.notice_utr, p.near_utr, p.notice_utr === p.near_utr],
  ];
  return rows;
}

export function matchCount(p) {
  const rows = unmatchedRows(p, true);
  return { same: rows.filter((r) => r[3]).length, total: rows.length, differ: rows.filter((r) => !r[3]).map((r) => r[0].toLowerCase()) };
}

// ---------------------------------------------------------------- evidence table
function evidence(p, panel, inflows, watch, short = false) {
  const sig = Object.fromEntries((panel?.signals || []).map((s) => [s.signal_name, s.signal_value]));
  const dwell = sig.median_dwell_minutes, pass = sig.pass_through_ratio ?? p.pass_through, large = sig.large_payer_share;
  const cash = sig.cash_or_crypto_exits ?? p.cash_exits, links = sig.complaint_linked_credits_7d ?? p.complaint_links;
  const rows = [];
  if (p.action_code === "PROPOSE_FULL_FREEZE") {
    const F = "For a freeze";
    if (links != null) rows.push(["Fraud complaints that paid it, 7 days", String(Math.round(n(links))), n(links) > 1 ? F : "Against: only one complaint", n(links) > 1]);
    if (pass != null) rows.push(["Leaves within an hour", `${Math.round(n(pass) * 100)}%`, n(pass) >= 0.5 ? F : "Against", n(pass) >= 0.5]);
    if (cash != null) rows.push(["Cash or crypto withdrawals", n(cash) ? String(Math.round(n(cash))) : "None", n(cash) ? F : "Against", n(cash) > 0]);
    const tries = attemptsOf(p, watch);
    if (tries.length) rows.push(["Tries to move held money", String(tries.length), F, true]);
    if (p.opened_on) { const d = ageDays(p.opened_on, p.held_since); rows.push(["Account age", ageWords(p.opened_on, p.held_since), d < 365 ? F : "Against", d < 365]); }
    const fresh = (watch || []).filter((e) => e.account_id === p.account_id && e.event_kind.startsWith("NEW_MONEY") && (!p.held_since || e.event_at > p.held_since));
    rows.push(["New money since the hold", fresh.length ? `${inrR(fresh.reduce((t, e) => t + n(e.amount_inr), 0))}` : `None since ${clock(p.held_since)}`,
      fresh.length ? F : "Against: a freeze blocks nothing yet", fresh.length > 0]);
    return rows;
  }
  const G = "Genuine";
  if (dwell != null) rows.push([short ? "Money stays" : "Money stays in the account", short ? stay(dwell) : stay(dwell).replace(" h", " hours").replace(" min", " minutes"), dwell >= 60 ? G : "Against", dwell >= 60]);
  if (pass != null) rows.push([short ? "Leaves within 1 h" : "Leaves within an hour", `${Math.round(n(pass) * 100)}%`, n(pass) < 0.5 ? G : "Against", n(pass) < 0.5]);
  if (large != null) rows.push(["Who pays it", large < 0.2 ? (short ? "Many small payers" : "Many small payers, like a shop") : large > 0.8 ? "A few large payers" : "A mix of payers", large < 0.5 ? G : "Against", large < 0.5]);
  if (cash != null) rows.push([short ? "Cash or crypto out" : "Cash or crypto withdrawals", n(cash) ? String(Math.round(n(cash))) : "None", n(cash) ? "Against" : G, !n(cash)]);
  const inn = inflows || [];
  const from = (x) => accountName(x.from_name).replace(/^Account /, "account ");
  if (inn.length) rows.push([short ? "Fraud payments in" : "Fraud payments received", inn.length === 1 ? `1: ${inrR(inn[0].amount_inr)} from ${from(inn[0])}${inn[0].ts && !short ? ` at ${clock(inn[0].ts)}` : ""}` : String(inn.length), "Against", false]);
  return rows;
}

function EvidenceTable({ rows, panel }) {
  if (!rows.length) return null;
  return (
    <div className="tw" style={{ marginBottom: 12 }}>
      <table className="t">
        <thead><tr><th scope="col">Evidence</th>{!panel && <th scope="col">This account</th>}{panel && <th scope="col">This account</th>}<th scope="col">Reads as</th></tr></thead>
        <tbody>{rows.map(([what, val, reads, good]) => (
          <tr key={what}><td>{what}</td><td>{val}</td><td><span className={`reads ${good ? "r-for" : "r-against"}`}>{reads}</span></td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- actions
function primaryLabel(p, variant, f) {
  switch (p.action_code) {
    case "PROPOSE_NARROW": return `Reduce to ${inrR(f.target)}`;
    case "PROPOSE_FULL_FREEZE": return variant === "panel" ? `Freeze ${shortName(p.name)}` : "Freeze the account";
    case "UNMATCHED_NOTICE": return `Accept and hold ${inrR(p.near_amount_inr || p.amount_inr)}`;
    default: return "Release the hold";
  }
}
function keepLabel(p, variant, f) {
  switch (p.action_code) {
    case "PROPOSE_NARROW": return `Keep ${inrR(f.held)}`;
    case "PROPOSE_FULL_FREEZE": return variant === "panel" ? "Keep the hold only" : `Keep the ${inrR(f.held)} hold only`;
    case "UNMATCHED_NOTICE": return "Wait for the police";
    default: return "Keep holding";
  }
}

export function Actions({ p, officer, onDone, variant = "wide", note }) {
  const f = figures(p);
  const [mode, setMode] = useState(null);       // null | approve | edit | reject
  const [signed, setSigned] = useState(p.draft_text || "");
  const [comment, setComment] = useState("");
  const [amount, setAmount] = useState(p.amount_inr ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const readOnly = !officer || SNAPSHOT;
  const signs = ["PROPOSE_NARROW", "PROPOSE_FULL_FREEZE"].includes(p.action_code);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      if (mode === "reject") await api.reject(p.proposal_id, { officer, comment });
      else await api.approve(p.proposal_id, { officer, comment: comment || null, amount: mode === "edit" ? Number(amount) : null,
        signed_text: signs && signed !== p.draft_text ? signed : null });
      setMode(null);
      onDone?.(p, mode);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (readOnly) {
    return (
      <>
        <div className="acts"><button className="btn" type="button" disabled>{primaryLabel(p, variant, f)}</button>
          <button className="btn" type="button" disabled>{keepLabel(p, variant, f)}</button></div>
        {!SNAPSHOT && <p style={{ fontSize: 14, marginTop: 10 }}>Only a signed-in nodal officer can decide, because their name goes on it. <a href="#/signin">Sign in</a></p>}
        {SNAPSHOT && <p className="note" style={{ marginTop: 10 }}>Saved copy: nothing can be decided here.</p>}
      </>
    );
  }
  if (mode === null) {
    return (
      <div className="acts">
        <button className="btn btn-p" type="button" onClick={() => setMode("approve")}>{primaryLabel(p, variant, f)}</button>
        {p.action_code === "PROPOSE_NARROW" && <button className="btn" type="button" onClick={() => setMode("edit")}>Change amount</button>}
        <button className="btn btn-q" type="button" onClick={() => setMode("reject")}>{keepLabel(p, variant, f)}</button>
        {note !== false && variant === "wide" && <span className="note signed">Signed as {officer}, with your reasons, in the audit log</span>}
      </div>
    );
  }
  return (
    <form className="form" onSubmit={(e) => { e.preventDefault(); send(); }}>
      {mode === "edit" && (
        <label>Hold this much instead (₹). At least the victims' money, less than what is held now.
          <input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        </label>
      )}
      {mode !== "reject" && signs && (
        <label>The reasons you sign (written from the account's evidence: change anything you would not sign)
          <textarea rows={6} value={signed} onChange={(e) => setSigned(e.target.value)} required />
        </label>
      )}
      <label>{mode === "reject" ? "Why not? (goes into the audit log)" : mode === "edit" ? "Why this amount? (required)" : "Note (optional)"}
        <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} required={mode !== "approve"} />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="acts">
        <button className="btn btn-p" disabled={busy}>{busy ? "Saving…" : mode === "reject" ? keepLabel(p, variant, f) : mode === "edit" ? "Hold this amount" : `Sign as ${officer}`}</button>
        <button type="button" className="btn btn-q" onClick={() => { setMode(null); setError(null); }}>Back</button>
      </div>
    </form>
  );
}

// "Hold again" / "Unfreeze" / "Release", each with a reason.
export function Undo({ p, officer, onDone, compact }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const u = UNDO[p.action_code];
  if (!u || !["APPROVED", "EDITED"].includes(p.decision_outcome || p.proposal_status) || p.undo_code || !officer || SNAPSHOT || !p.decision_id) return null;
  if (!open) return <button className={`btn btn-q${compact ? " btn-s" : ""}`} type="button" onClick={() => setOpen(true)}>{compact ? u[0] : `${u[0]}, with a reason`}</button>;
  return (
    <form className="form" style={{ width: "100%" }} onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      setError(null);
      try { await api.undo(p.decision_id, { officer, reason }); setOpen(false); onDone?.(); } catch (x) { setError(x.message); } finally { setBusy(false); }
    }}>
      <label>{p.action_code === "PROPOSE_NARROW" ? "Released money may already be spent: a new hold covers only what is still in the account. Why?" : "Why? (goes into the audit log and the hold record)"}
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} required />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="acts"><button className="btn btn-p" disabled={busy}>{busy ? "Saving…" : `${u[0]} as ${officer}`}</button>
        <button className="btn btn-q" type="button" onClick={() => setOpen(false)}>Back</button></div>
    </form>
  );
}

// ---------------------------------------------------------------- the decided state
export function decidedTitle(p) {
  const who = shortName(p.name), amt = inrR(p.decided_amount_inr ?? p.amount_inr);
  if ((p.decision_outcome || p.proposal_status) === "REJECTED") return p.action_code === "UNMATCHED_NOTICE" ? "Left with the police" : `${who}: kept as it was`;
  return { PROPOSE_NARROW: `${who}'s hold reduced to ${amt}`, PROPOSE_FULL_FREEZE: `${who} frozen, ${amt}`,
    UNMATCHED_NOTICE: `Near match accepted, ${amt} held`, PROPOSE_RELEASE: `${who}: hold released` }[p.action_code] || who;
}

export function Decided({ p, officer, onDone }) {
  return (
    <article className="card dc narrowcard">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
        <span className="kind" style={{ color: "#44505e" }}>{KIND[p.action_code]?.split(" · ")[0]}</span><span className="pill p-done">Decided</span>
      </div>
      <h3 style={{ fontSize: 19 }}>{decidedTitle(p)}</h3>
      <p className="note" style={{ marginBottom: 12 }}>{shortName(p.name)} <span className="id">{p.account_id}</span> · by {p.decided_by} at {clock(p.decided_at)}</p>
      {(p.signed_text || p.decided_reason) && (
        <div className="decided" style={{ marginBottom: 12 }}>
          <p className="label">Reasons {p.decided_by} signed</p>
          <p style={{ whiteSpace: "pre-wrap" }}>{rs(p.signed_text || p.decided_reason)}</p>
          {p.signed_text && p.draft_text && p.signed_text !== p.draft_text && <p className="note" style={{ marginTop: 6 }}>{p.decided_by} edited the suggested reasons.</p>}
        </div>
      )}
      {!n(p.victim_amount_inr) && p.action_code === "PROPOSE_FULL_FREEZE" && <p style={{ marginBottom: 12 }}><b>Not counted as {figures(p).victim}'s money.</b> {shortName(p.name)} was never on the trail.</p>}
      {p.undo_code && <p style={{ marginBottom: 12 }}><b>Undone:</b> {UNDO[p.action_code]?.[0].toLowerCase()} by {p.undone_by} at {clock(p.undone_at)}: "{p.undo_reason}"</p>}
      <div className="acts"><a className="btn" href="#/audit">In the audit log</a><Undo p={p} officer={officer} onDone={onDone} /></div>
    </article>
  );
}

// ---------------------------------------------------------------- the card
function Header({ p, pill, pillClass }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "8px 16px", marginBottom: 12 }}>
      <div>
        <p style={{ fontSize: 17, fontWeight: 700 }}>{accountName(p.name)}</p>
        <p className="note">{p.bank_name || "Bank X"} {p.account_type} account{p.opened_on ? `, open ${ageWords(p.opened_on, p.held_since || p.proposed_at)}` : ""} · <a href={`#/case/${encodeURIComponent(p.case_id)}`}>{p.label}, {inrR(p.complaint_amount_inr)}, {figures(p).victim}</a></p>
      </div>
      {pill && <span className={`pill ${pillClass}`} style={{ alignSelf: "flex-start" }}>{pill}</span>}
    </div>
  );
}

function Since({ p, watch }) {
  const ev = (watch || []).filter((e) => e.account_id === p.account_id && KEY_EVENTS.includes(e.event_kind) && (!p.held_since || e.event_at >= p.held_since));
  if (!ev.length) return null;
  const shown = ev.slice(0, 3);
  return <p style={{ fontSize: 14, marginBottom: 10 }}><b>Since the hold:</b> {shown.map((e, i) => <span key={e.event_id}>{i ? "; " : ""}<span className="mono">{clock(e.event_at)}</span> {i ? shortEvent(e) : cap(shortEvent(e))}</span>)}.
    {ev.length > 3 && <> <a href={`#/record/${encodeURIComponent(p.account_id)}`}>{ev.length - 3} more in the hold record</a></>}</p>;
}

function Changed({ p }) {
  if (!balanceChanged(p)) return null;
  return (
    <div className="changed"><b>Changed while you were reading</b><br />The balance moved from {inrR(p.balance_now_inr)} to {inrR(p.balance_today_inr)}. The figures below are updated. Your choice is unchanged.</div>
  );
}

export default function DecisionCard({ p, officer, onDone, watch, variant = "wide", position }) {
  const needsPanel = p.action_code !== "UNMATCHED_NOTICE";
  const panel = useLoad(() => (needsPanel ? api.account(p.case_id, p.account_id).catch(() => null) : Promise.resolve(null)), [p.case_id, p.account_id]);
  const pd = panel.data;
  const f = figures(p);
  if (balanceChanged(p)) f.balance = n(p.balance_today_inr);
  const rows = needsPanel ? evidence(p, pd, pd?.inflows, watch, variant === "panel") : [];
  const wide = variant === "wide";
  const legal = p.action_code === "PROPOSE_NARROW" ? p.citation : LEGAL[wide ? "wide" : "panel"][p.action_code] || p.citation;

  if (p.action_code === "UNMATCHED_NOTICE") {
    const tbl = unmatchedRows(p, !wide);
    const sames = tbl.filter((r) => r[3]).map((r) => r[0].toLowerCase());
    const gap = p.received_at && p.near_at ? Math.round((new Date(String(p.received_at).replace(" ", "T")) - new Date(String(p.near_at).replace(" ", "T"))) / 60000) : null;
    const differ = tbl.filter((r) => !r[3]).length;
    return (
      <article className={`card dc ${wide ? "" : "narrowcard red"}`} style={wide ? undefined : { borderColor: "#e7b3ae" }}>
        {!wide && <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}><span className="kind red">{KIND.UNMATCHED_NOTICE}</span><span className="pill p-urgent">Waiting</span></div>}
        {wide ? <Header p={p} pill="Nothing held" pillClass="p-urgent" /> : null}
        <h4 style={{ fontSize: 19, marginBottom: wide ? 12 : 2 }}>{wide ? "Is this the payment the police meant?" : `Hold ${inrR(p.near_amount_inr)} in ${shortName(p.name)} on a notice that does not fully match?`}</h4>
        {!wide && <p className="note" style={{ marginBottom: 12 }}>{f.victim}'s complaint, {clock(p.received_at)} · {shortName(p.name)} <span className="id">{p.account_id}</span></p>}
        <div className="tw" style={{ marginBottom: 14 }}>
          <table className="t">
            <thead><tr><th scope="col"></th><th scope="col">{wide ? "On the notice" : "Notice"}</th><th scope="col">{wide ? `Payment into ${shortName(p.name)}` : "Payment"}</th><th scope="col">{wide ? "Match" : ""}</th></tr></thead>
            <tbody>
              {tbl.map(([what, a, b, same, word]) => (
                <tr key={what}><td className="label">{what}</td><td className={what === "UTR" ? "mono" : ""} style={what === "UTR" ? { fontSize: 13 } : undefined}>{what === "UTR" && !wide ? `…${String(a).slice(-4)}` : a}</td>
                  <td className={what === "UTR" ? "mono" : ""} style={what === "UTR" ? { fontSize: 13 } : undefined}>{what === "UTR" && !wide ? `…${String(b).slice(-4)}` : b}</td>
                  <td><span className={`reads ${same ? "r-same" : wide ? "r-against" : "r-diff"}`}>{same ? word || "Same" : "Different"}</span></td></tr>
              ))}
              <tr><td className="label">Still in {shortName(p.name)}{wide ? " now" : ""}</td><td>{wide ? "" : inrR(p.balance_today_inr)}</td><td>{wide ? <b>{inrR(p.balance_today_inr)}</b> : ""}</td><td></td></tr>
            </tbody>
          </table>
        </div>
        {wide ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "12px 24px", marginBottom: 12 }}>
            <div>
              <p style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>Why it is probably the same payment</p>
              <ul style={{ paddingLeft: 20, fontSize: 14 }}>{sames.length > 0 && <li>Same {sames.join(", ").replace("paid at", "minute").replace(/, ([^,]*)$/, " and $1")}.</li>}{gap != null && gap > 0 && <li>It arrived {gap} minutes before the complaint.</li>}</ul>
            </div>
            <div>
              <p style={{ fontSize: 14, fontWeight: 700, marginBottom: 4, color: "#6b4200" }}>What argues against it</p>
              <ul style={{ paddingLeft: 20, fontSize: 14 }}>{p.notice_utr !== p.near_utr && <li>A UTR identifies one payment. A different UTR can mean a different payment; the police may send a corrected notice instead.</li>}
                {tbl.filter((r) => !r[3] && r[0] !== "UTR").map((r) => <li key={r[0]}>The {r[0].toLowerCase()} differs.</li>)}</ul>
            </div>
          </div>
        ) : (
          <>
            <div className="against" style={{ marginBottom: 12 }}><b>Against holding:</b> {["no", "one", "two", "three", "four", "five"][differ]} of {tbl.length === 5 ? "five" : tbl.length} details {differ === 1 ? "differs" : "differ"}. This may be another person's payment, and a hold here would rest on our reading, not the police notice.</div>
            <p style={{ fontSize: 14, marginBottom: 10 }}><b>Since the notice:</b> sent back to the police at {clock(p.proposed_at)} for a corrected UTR. No reply yet.</p>
          </>
        )}
        <p className="note" style={{ marginBottom: 14 }}>Legal basis: {legal}</p>
        <Actions p={p} officer={officer} onDone={onDone} variant={variant} />
      </article>
    );
  }

  if (p.action_code === "PROPOSE_FULL_FREEZE") {
    const who = shortName(p.name);
    const victimShare = n(p.victim_amount_inr);
    return (
      <article className={`card dc ${wide ? "" : "narrowcard"}`}>
        {!wide && <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}><span className="kind">{position || KIND.PROPOSE_FULL_FREEZE}</span><span className="pill p-wait">Waiting</span></div>}
        {wide && <Header p={p} pill="Pass-through pattern" pillClass="p-held" />}
        <Changed p={p} />
        <h4 style={{ fontSize: 19, marginBottom: wide ? 12 : 2 }}>{wide ? `Freeze ${who}'s full account?` : `Freeze ${who}'s whole account?`}</h4>
        {!wide && <p className="note" style={{ marginBottom: 12 }}>{who} <span className="id">{p.account_id}</span> · {p.account_type}, opened {String(p.opened_on || "").slice(0, 10)}</p>}
        {wide ? (
          <div className="nowfreeze">
            <div><p className="label">Now</p><p style={{ fontSize: 15 }}><b>{inrR(f.held)} held</b>{f.held >= f.balance ? ", the whole balance" : ` of a ${inrR(f.balance)} balance`}{victimShare >= f.held ? `, all of it ${f.victim}'s` : ""}.</p><p className="note">Money that comes in later is not held.</p></div>
            <div><p className="label">With a freeze</p><p style={{ fontSize: 15 }}><b>The same {inrR(Math.max(f.held, f.balance))}</b>, plus every new credit is blocked.</p><p className="note">Until a police direction, at most 90 days.</p></div>
          </div>
        ) : (
          <div className="tw" style={{ marginBottom: 12 }}>
            <table className="t">
              <thead><tr><th scope="col"></th><th scope="col">Now</th><th scope="col">With a freeze</th></tr></thead>
              <tbody>
                <tr><td>Money in {who}</td><td>{inrR(f.held)} held</td><td>{inrR(Math.max(f.held, f.balance))} held</td></tr>
                <tr><td>New money in</td><td>Can leave</td><td><b>Blocked</b></td></tr>
                {f.balance > f.held && <tr><td>Payments out</td><td>Up to {inrR(f.balance - f.held)}</td><td>None</td></tr>}
              </tbody>
            </table>
          </div>
        )}
        <EvidenceTable rows={wide ? rows : rows.filter((r) => !r[0].startsWith("New money"))} panel={!wide} />
        {!wide && <div className="against" style={{ marginBottom: 12 }}><b>Against freezing:</b> a freeze blocks nothing yet. Every rupee in {who} is already held. It only matters if new money arrives, and a full freeze is harder to defend in court than a hold.</div>}
        <Since p={p} watch={watch} />
        <p className="note" style={{ marginBottom: 14 }}>Legal basis: {legal}</p>
        <Actions p={p} officer={officer} onDone={onDone} variant={variant} note={false} />
      </article>
    );
  }

  // smaller hold (and the rare release)
  const who = shortName(p.name);
  if (variant === "inlist") {
    const [clear, why] = clearCase(p);
    const from = pd?.inflows?.[0]?.from_name;
    const against = clear
      ? `the ${inrR(f.held)} came straight from ${from || "an account already held"}. Keeping the full hold costs ${who} ${inrR(f.release)} until the police reply.`
      : /cash/.test(why || "") ? "this account regularly takes cash out. Money released today could leave as cash the same day. That's why it isn't a clear case."
        : /under 1 year/.test(why || "") ? "the account is under a year old, so its history is short. That's why it isn't a clear case."
          : "the victim's share is a large part of the balance, so this is less like one stray payment. That's why it isn't a clear case.";
    return (
      <article className="card" style={{ padding: "16px 18px", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "14px 24px" }}>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}><span className="kind">{position}</span><span className="pill p-wait">Waiting</span></div>
          <h4 style={{ fontSize: 19, marginBottom: 10 }}>Reduce {possessiveOf(who)} hold to {inrR(f.target)}?</h4>
          <Changed p={p} />
          <div className="facts" style={{ marginBottom: 12 }}>
            <span>Held now</span><b style={{ textAlign: "right" }}>{inrR(f.held)}</b>
            <span className="sub v">{f.victim}'s</span><b className="v" style={{ textAlign: "right" }}>{inrR(f.target)}</b>
            <span className="sub g">Their own</span><b className="g" style={{ textAlign: "right" }}>{inrR(f.release)}</b>
          </div>
          <div className="against" style={{ marginBottom: 12 }}><b>Against reducing:</b> {against}</div>
          <p className="note">Legal basis: {rs(legal || "")}</p>
        </div>
        <div>
          <EvidenceTable rows={rows} />
          <Actions p={p} officer={officer} onDone={onDone} variant="panel" note={false} />
          <p className="note" style={{ marginTop: 10 }}>After you decide, this row closes to one line and the next row that needs a look opens.</p>
        </div>
      </article>
    );
  }
  const vPct = f.held ? Math.max(8, Math.min(92, (f.target / f.held) * 100)) : 50;
  return (
    <article className={`card dc ${wide ? "" : "narrowcard"}`}>
      {!wide && position && <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}><span className="kind">{position}</span><span className="pill p-wait">Waiting</span></div>}
      {wide && <Header p={p} pill={p.band === "HONEST" ? "Likely genuine customer" : "Mixed history"} pillClass={p.band === "HONEST" ? "p-honest" : "p-plain"} />}
      <Changed p={p} />
      <h4 style={{ fontSize: 19, marginBottom: wide ? 12 : 4 }}>{p.action_code === "PROPOSE_RELEASE" ? `Release the hold on ${who}?` : wide ? `Reduce the hold from ${inrR(f.held)} to ${inrR(f.target)}?` : `Reduce ${who}'s hold to ${inrR(f.target)}?`}</h4>
      {!wide && <p className="note" style={{ marginBottom: 12 }}>{p.name}, {p.bank_name || "Bank X"} {p.account_type} account, open {ageWords(p.opened_on, p.held_since)}</p>}
      {p.action_code === "PROPOSE_NARROW" && wide && (
        <>
          <div className="bar" role="img" aria-label={`Of ${inrR(f.held)} held, ${inrR(f.target)} is the victim's and ${inrR(f.release)} is the customer's own`}>
            <div style={{ flex: `0 0 ${vPct}%`, background: "#5b3fa0" }}>Victim's {inrR(f.target)}</div>
            <div style={{ flex: "1 1 auto", background: "#2f7a5a" }}>Customer's own {inrR(f.release)}, released</div>
          </div>
          <p className="note" style={{ margin: "6px 0 14px" }}>Held now {inrR(f.held)} of a {inrR(f.balance)} balance. The other {inrR(Math.max(0, f.balance - f.held))} was never held.</p>
        </>
      )}
      {p.action_code === "PROPOSE_NARROW" && !wide && (
        <div className="facts" style={{ marginBottom: 12 }}>
          <span>Balance</span><b style={{ textAlign: "right" }}>{inrR(f.balance)}</b>
          <span>Held since {clock(p.held_since)}</span><b style={{ textAlign: "right" }}>{inrR(f.held)}</b>
          <span className="sub v">of which {f.victim}'s</span><b className="v" style={{ textAlign: "right" }}>{inrR(f.target)}</b>
          <span className="sub g">of which their own</span><b className="g" style={{ textAlign: "right" }}>{inrR(f.release)}</b>
          <span className="line">Can use now</span><b className="line" style={{ textAlign: "right" }}>{inrR(Math.max(0, f.balance - f.held))}</b>
          <span>Can use if you approve</span><b className="g" style={{ textAlign: "right" }}>{inrR(Math.max(0, f.balance - f.target))}</b>
        </div>
      )}
      <EvidenceTable rows={rows} />
      <Since p={p} watch={watch} />
      <p className="note" style={{ marginBottom: 14 }}>Legal basis: {rs(legal || "")}</p>
      <Actions p={p} officer={officer} onDone={onDone} variant={variant} note={false} />
      {!wide && <p className="note" style={{ marginTop: 12 }}>Signed as {officer || "the signed-in officer"}, with your reasons, in the audit log and the hold record. <a href={`#/record/${encodeURIComponent(p.account_id)}`}>Hold record</a></p>}
    </article>
  );
}

function possessiveOf(s) {
  return s.endsWith("s") ? `${s}'` : `${s}'s`;
}

export { cap };