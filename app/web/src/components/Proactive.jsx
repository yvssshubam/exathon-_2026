import { createContext, useContext, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, SNAPSHOT } from "../api.js";
import { accountName, clock, dayTime, inrR, plural, rs, shortName } from "../format.js";
import { useLoad } from "../hooks.js";
import { evidence, EvidenceTable, typeName } from "./DecisionCard.jsx";
import SidePanel, { PanelFooter } from "./SidePanel.jsx";

// Proactive holds on Review (D-60 to D-65; design brief Module 6). The money was held the moment a first-time payer's
// payment landed in an account a victim had already named; the officer checks each hold the next working day and
// makes the real decision at the explanation stage. Nothing waits for the officer at 2 a.m.

const n = (v) => Number(v || 0);

// Which complaint a row belongs to ("02:13 · Lakshmi"), and the complaint the officer jumped to from Today's
// complaints, whose rows are highlighted. Review provides it; every decision group reads it.
export const Complaints = createContext({ who: () => null, focus: null });
// The complaint a decision comes from, as a link to it: "Lakshmi, 02:13". A Suspect Registry watch has no complaint.
export function ComplaintLink({ caseId, fallback }) {
  const { who } = useContext(Complaints);
  if (String(caseId || "").startsWith("REGISTRY:")) return <span className="note">Suspect Registry</span>;
  const text = who(caseId) || fallback || caseId;
  return <a href={`#/case/${encodeURIComponent(caseId)}`} style={{ whiteSpace: "nowrap" }}>{text}</a>;
}
export function useRowClass() {
  const { focus } = useContext(Complaints);
  return (caseId, open) => `pick${open ? " open" : ""}${focus && caseId === focus ? " focus" : ""}`;
}
const CHECK_WORDS = {
  amount: "₹1,000 or more", new_payer: "First payment from this payer in 90 days", not_automatic: "Not NACH or AutoPay",
  not_reversal: "Not a return of its own payment", not_already_named: "No complaint names it yet",
  fast_mover_extra: "Fast mover: one extra condition", unusually_large: "Larger than 90% of its credits",
  payer_flagged: "The payer is flagged", close_to_the_fraud: "Within 24 h of the last reported payment",
};
const PAYER = { other_bank: "a customer of another bank", own_customer: "our own customer", flagged: "a flagged account" };
const ALERT = {
  sent: ["Pre-alert sent", "p-done"], waiting_for_officer: ["Pre-alert waiting for you", "p-urgent"],
  answered: ["The payer's complaint arrived", "p-clear"], confirmed: ["The payer says it was fraud", "p-held"],
  genuine: ["The payer says it was genuine", "p-wait"], not_needed: ["Not needed", "p-done"],
};
const BASIS = { rbi_draft: "RBI draft: temporary debit hold", police_notice: "Under the existing police notice" };

function Badge({ h }) {
  return h.with_department_head ? <span className="pill p-urgent" style={{ marginLeft: 6 }}>With department head</span> : null;
}

// One officer action: a name and a reason, always.
function Act({ label, primary, officer, reason: initial, run, onDone, danger }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState(initial || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const readOnly = !officer || SNAPSHOT;
  if (readOnly) return <button className="btn" type="button" disabled>{label}</button>;
  if (!open) return <button className={`btn ${primary ? "btn-p" : danger ? "btn-q" : ""}`} type="button" onClick={() => setOpen(true)}>{label}</button>;
  async function go(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await run({ officer, reason });
      setOpen(false);
      onDone?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="form" onSubmit={go} style={{ flex: "1 1 100%" }}>
      <label>{label}: your reason (goes into the audit log)
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} required />
      </label>
      <div className="acts">
        <button className={`btn ${primary ? "btn-p" : ""}`} type="submit" disabled={busy || !reason.trim()}>{busy ? "Recording…" : `${label}, signed as ${officer}`}</button>
        <button className="linkbtn" type="button" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      {error && <p role="alert" style={{ color: "#7a211b", fontSize: 14 }}>{error}</p>}
    </form>
  );
}

// ---------------------------------------------------------------- one proactive hold
// Shown in the side panel, like a decision card: its buttons go to the panel's pinned footer (PinnedActions in
// DecisionCard), so they stay on screen while the officer reads the checks and the clocks.
function Pinned({ children }) {
  const footer = useContext(PanelFooter);
  return footer ? createPortal(children, footer) : children;
}

// The same "What we checked" table as a decision card, with its collapsible sections: why the account can be held
// at all, the payment's own checks on arrival, then the account's history and its two scores' signals.
const PRO_SECTIONS = [["held", "Why the account can be held"], ["checks", "The payment's checks"],
  ["history", "The account's history"], ["pass", "How money moves"], ["exposure", "Where its money comes from"]];
const ELIGIBLE = { layer1_ticket: "A victim paid it directly (a complaint names it)", suspect_registry: "Listed in the Suspect Registry" };

function proactiveRows(h, panel) {
  const rows = [];
  const named = String(h.case_id || "").startsWith("REGISTRY:") ? "suspect_registry" : "layer1_ticket";
  rows.push(["Why it is on the watch", ELIGIBLE[h.eligibility_reason || named], "Can be held", false,
    "Only accounts a complaint names directly, or in the Suspect Registry, can get a hold before anyone complains.", "held"]);
  rows.push(["Account type", `${typeName(h.account_pattern)}`, ["PIPE_FOR_FRAUD", "FAST_MOVER"].includes(h.account_pattern) ? "Can be held" : "Stops the hold",
    !["PIPE_FOR_FRAUD", "FAST_MOVER"].includes(h.account_pattern),
    "Only a Pipe for fraud or a Fast mover. The score can stop a hold, never start one.", "held"]);
  for (const c of h.checks || []) {
    if (c.check === "fast_mover_extra") continue;
    rows.push([CHECK_WORDS[c.check] || c.check, c.value != null && c.value !== "" ? rs(String(c.value)) : c.passed ? "Yes" : "No",
      c.passed ? "Passed" : "Not met", c.passed, undefined, "checks"]);
  }
  // the account's own signals and history, worded exactly as on a decision card
  const p = { action_code: "PROACTIVE_HOLD", opened_on: h.opened_on, held_since: h.placed_at };
  for (const r of evidence(p, panel, [], [], true)) if (r[5] !== "held") rows.push(r);
  return rows;
}

export function ProactiveCard({ h, officer, onDone, position }) {
  const [show, setShow] = useState(false);
  const who = shortName(h.name);
  const rows = proactiveRows(h, { signals: h.signals || [] });       // the scores as they stood at the hold
  const checks = (h.checks || []).filter((c) => c.check !== "fast_mover_extra");
  const statusOf = (key, list) => (key === "checks" ? `${checks.filter((c) => c.passed).length} of ${checks.length} passed`
    : key === "held" ? (list.every((r) => !r[3]) ? "Can be held" : "Cannot be held") : null);
  const a = h.pre_alert;
  const [alertLabel, alertCls] = a ? ALERT[a.status] || [a.status, "p-plain"] : h.payer_type === "flagged" ? ["Layering: recorded for the police", "p-plain"] : [null, null];
  const fast = h.account_pattern === "FAST_MOVER";
  const ref = `PH-H${h.hold_id}`;
  return (
    <article className="card dc narrowcard">
      {position && <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}><span className="kind">{position}</span><span className="pill p-wait">Waiting</span></div>}
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "8px 16px", marginBottom: 10 }}>
        <div>
          <p style={{ fontSize: 17, fontWeight: 700 }}>{accountName(h.name)} <span className="id">{h.account_id}</span></p>
          <p className="note">{h.case_id.startsWith("REGISTRY:") ? "In the Suspect Registry" : <>Named by <a href={`#/case/${encodeURIComponent(h.case_id)}`}>{h.label}</a></>} · hold <a href={`#/record/${encodeURIComponent(h.account_id)}`}>{ref}</a></p>
        </div>
        <span style={{ alignSelf: "flex-start" }}><span className={`pill ${h.account_pattern === "PIPE_FOR_FRAUD" ? "p-held" : "p-wait"}`}>{typeName(h.account_pattern)}</span><Badge h={h} /></span>
      </div>
      <h4 style={{ fontSize: 19, marginBottom: 6 }}>{inrR(h.amount_inr)} held at {clock(h.placed_at)}, before anyone complained</h4>
      <p style={{ fontSize: 14, marginBottom: 12 }}>
        {inrR(h.amount_inr)} {h.channel ? `by ${h.channel} ` : ""}from {PAYER[h.payer_type] || "a payer"}{h.payer_bank && h.payer_type === "other_bank" ? ` (${h.payer_bank}, ending ${h.payer_ending})` : h.payer_ending ? ` (ending ${h.payer_ending})` : ""}, who had not paid {who} before. UTR <span className="mono" style={{ fontSize: 13 }}>{h.utr || "–"}</span>.
      </p>
      <EvidenceTable rows={rows} sections={PRO_SECTIONS} statusOf={statusOf}
        score={{ pass_through: h.pass_through, fraud_exposure: h.fraud_exposure, account_pattern: h.account_pattern }} />
      <div className="facts" style={{ marginBottom: 12 }}>
        <span>Legal basis</span><b style={{ textAlign: "right" }}>{BASIS[h.legal_basis] || h.legal_basis}{h.hold_mode === "current_sop" ? " (current SOP mode)" : ""}</b>
        {h.legal_basis === "rbi_draft" && <><span>Holder can explain until</span><b style={{ textAlign: "right" }}>{dayTime(h.explanation_due_on)}</b>
          <span>Decide by</span><b style={{ textAlign: "right" }}>{dayTime(h.decide_by)}</b>
          <span>Days left on the clock</span><b style={{ textAlign: "right" }}>{h.days_left ?? "–"} of 60</b></>}
        {h.review_state === "NEXT_DAY_CHECK" && <><span>Next-day check due</span><b style={{ textAlign: "right" }}>{dayTime(h.check_due_at)}</b></>}
      </div>
      {alertLabel && <p style={{ fontSize: 14, marginBottom: 10 }}><span className={`pill ${alertCls}`}>{alertLabel}</span>{a?.route ? <span className="note"> · {a.route === "fraud_team" ? "a call by our fraud team" : "to the police through CFCFRMS"}</span> : null}{a?.minutes_early != null ? <span className="note"> · held {plural(a.minutes_early, "minute")} before the complaint</span> : null}
        {a?.text && <> · <button className="linkbtn" type="button" onClick={() => setShow(!show)}>{show ? "Hide the text" : "Read the text"}</button></>}</p>}
      {show && a?.text && <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: 13, marginBottom: 12, padding: "12px 14px", background: "#f5f7f9", border: "1px solid #e8ecf0", borderRadius: 8 }}>{a.text}</pre>}
      {h.hold_status === "ACTIVE" && h.review_state !== "LINKED" && (
        <Pinned><div className="acts" style={{ flexWrap: "wrap" }}>
          {h.review_state === "NEXT_DAY_CHECK" && <Act label="Confirm the hold" primary officer={officer} reason={`Checked: ${typeName(h.account_pattern)}, first-time payer, ${inrR(h.amount_inr)}.`}
            run={(b) => api.confirmHold(h.hold_id, b)} onDone={onDone} />}
          {fast && a?.status === "waiting_for_officer" && <Act label="Send the pre-alert" primary={h.review_state !== "NEXT_DAY_CHECK"} officer={officer}
            reason="The payer should be asked whether they meant to pay." run={(b) => api.sendPreAlert(a.pre_alert_id, b)} onDone={onDone} />}
          {h.legal_basis === "rbi_draft" && ["EXPLAINED", "REFER_DRAFTED", "CONFIRMED", "GENUINE"].includes(h.review_state) && h.item &&
            <Act label="Keep and refer to the police" primary={h.item === "refer_drafted"} officer={officer} reason={(h.reasons || []).slice(0, 2).join(" ")}
              run={(b) => api.keepAndRefer(h.hold_id, b)} onDone={onDone} />}
          <Act label="Release" danger officer={officer} reason={h.review_state === "GENUINE" ? "The payer says the payment was genuine." : ""}
            run={(b) => api.releaseHold(h.hold_id, b)} onDone={onDone} />
        </div></Pinned>
      )}
      {!officer && !SNAPSHOT && <p style={{ fontSize: 14, marginTop: 10 }}>Only a signed-in nodal officer can decide, because their name goes on it. <a href="#/signin">Sign in</a></p>}
    </article>
  );
}

// ---------------------------------------------------------------- the morning list
// The same shape as the other groups on Review: a list card with one row per hold and an Open button; the hold
// opens in the page's one side panel (openId "pro-<hold id>", so it never clashes with a proposal's number).
const proKey = (h) => `pro-${h.hold_id}`;

function HoldRows({ list, cols, row, openId, setOpenId, title, officer, onDone, listRef }) {
  const rowClass = useRowClass();
  const openH = list.find((h) => proKey(h) === openId) || null;
  // after a decision the next hold in the list opens, as in the other groups
  const decided = (h) => { const next = list.find((x) => x.hold_id !== h.hold_id); setOpenId(next ? proKey(next) : null); onDone?.(); };
  return (
    <>
      <div className="tw" ref={listRef}>
        <table className="t">
          <thead><tr>{cols.map(([label, right]) => <th key={label} scope="col" className={right ? "r" : undefined}>{label}</th>)}<th /></tr></thead>
          <tbody>
            {list.map((h) => {
              const isOpen = openId === proKey(h);
              const pick = (e) => { if (!e.target.closest("button, a, input, label, select")) setOpenId(proKey(h)); };
              return (
                <tr key={h.hold_id} className={rowClass(h.case_id, isOpen)} onClick={pick}>
                  {row(h)}
                  <td className="r"><button className="btn btn-s" type="button" aria-expanded={isOpen}
                    onClick={() => setOpenId(isOpen ? null : proKey(h))}>{isOpen ? "Close" : "Open"}</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {openH && (
        <SidePanel title={title} itemKey={proKey(openH)} onClose={() => setOpenId(null)} beside={listRef}>
          <ProactiveCard key={openH.hold_id} h={openH} officer={officer} onDone={() => decided(openH)}
            position={`${openH.item_title || "Held overnight"} · ${list.indexOf(openH) + 1} of ${list.length}`} />
        </SidePanel>
      )}
    </>
  );
}

function alertCell(h) {
  const a = h.pre_alert;
  if (!a) return h.payer_type === "flagged" ? <span className="pill p-plain">Layering</span> : <span className="note">–</span>;
  const [label, cls] = ALERT[a.status] || [a.status, "p-plain"];
  return <span className={`pill ${cls}`}>{label}</span>;
}

export function HeldOvernight({ data, officer, onDone, openId, setOpenId }) {
  const list = data?.held_overnight || [];
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const listRef = useRef(null);
  if (!list.length) return null;
  const clear = list.filter((h) => h.clear_case);
  const rows = [...list.filter((h) => !h.clear_case), ...clear];      // the ones to look at first
  async function confirmAll() {
    setBusy(true);
    setNote(null);
    try {
      const r = await api.confirmClear({ officer, reason: "Clear case: Pipe for fraud, both scores outside the grey zone, no answer from the payer yet.", proactive_ids: clear.map((h) => h.hold_id) });
      setNote(`${plural(r.confirmed.length, "hold")} confirmed, each recorded on its own${r.refused.length ? `; ${r.refused.length} not clear, open it to confirm` : ""}.`);
      setOpenId(null);
      onDone?.();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }
  const total = list.reduce((t, h) => t + n(h.amount_inr), 0);
  return (
    <div id="g-overnight" style={{ marginTop: 12, scrollMarginTop: 110 }}>
      <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="lg-overnight">
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: "10px 16px", marginBottom: 12 }}>
          <div>
            <h3 id="lg-overnight" style={{ fontSize: 20 }}>Held overnight · {list.length}</h3>
            <p className="note" style={{ fontSize: 14 }}>{inrR(total)} held before anyone complained. {data.mode === "rbi_draft" ? "Draft mode: RBI draft KYC directions." : "Current SOP mode."}</p>
          </div>
        </div>
        <HoldRows list={rows} openId={openId} setOpenId={setOpenId} officer={officer} onDone={onDone} listRef={listRef} title="Held overnight"
          cols={[["Account"], ["Complaint"], ["Held", true], ["Paid at"], ["Payer"], ["Type"], ["Pre-alert"]]}
          row={(h) => <>
            <td><b>{shortName(h.name)}</b>{h.with_department_head && <><br /><span className="pill p-urgent" style={{ marginTop: 6 }}>With department head</span></>}</td>
            <td><ComplaintLink caseId={h.case_id} fallback={h.label} /></td>
            <td className="r" style={{ fontWeight: 700 }}>{inrR(h.amount_inr)}</td>
            <td><span className="mono" style={{ whiteSpace: "nowrap" }}>{dayTime(h.placed_at)}</span></td>
            <td>{h.payer_type === "own_customer" ? "Our customer" : h.payer_type === "other_bank" ? (h.payer_bank || "Another bank") : "Flagged account"}</td>
            <td>{typeName(h.account_pattern)}{h.clear_case && <><br /><span className="pill p-clear" style={{ marginTop: 6 }}>Ready to confirm</span></>}</td>
            <td>{alertCell(h)}</td>
          </>} />
        <p className="note" style={{ marginTop: 8 }}>A first-time payer's money into an account a victim had already named, held the minute it landed. Confirm or release each one; the holder can explain within 20 days and you decide then. Ready to confirm: Pipe for fraud, both scores outside the grey zone, no answer from the payer yet.</p>
        {clear.length > 0 && (
          <div className="selbar">
            <span><b>{plural(clear.length, "hold")} ready to confirm</b> · {inrR(clear.reduce((t, h) => t + n(h.amount_inr), 0))}</span>
            <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
              {!officer && <span className="note">Sign in to confirm.</span>}
              <button className="btn btn-p" type="button" disabled={busy || !officer || !!SNAPSHOT} onClick={confirmAll}>{busy ? "Confirming…" : `Confirm all ${clear.length}`}</button>
            </span>
          </div>
        )}
        {note && <p role="status" style={{ fontSize: 14, marginTop: 8 }}>{note}</p>}
      </section>
    </div>
  );
}

export function ProactiveQueue({ data, officer, onDone, openId, setOpenId }) {
  const items = data?.queue || [];
  const listRef = useRef(null);
  if (!items.length) return null;
  return (
    <div id="g-clock" style={{ marginTop: 26, scrollMarginTop: 110 }}>
      <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="lg-clock">
        <div style={{ marginBottom: 12 }}>
          <h3 id="lg-clock" style={{ fontSize: 20 }}>Proactive holds: decisions due · {items.length}</h3>
          <p className="note" style={{ fontSize: 14 }}>Never released early without a decision, never kept past 60 days without one.</p>
        </div>
        <HoldRows list={items} openId={openId} setOpenId={setOpenId} officer={officer} onDone={onDone} listRef={listRef} title="Proactive hold"
          cols={[["Account"], ["Complaint"], ["Held", true], ["To decide"], ["Decide by"], ["Days left", true]]}
          row={(h) => <>
            <td><b>{shortName(h.name)}</b>{h.with_department_head && <><br /><span className="pill p-urgent" style={{ marginTop: 6 }}>With department head</span></>}</td>
            <td><ComplaintLink caseId={h.case_id} fallback={h.label} /></td>
            <td className="r" style={{ fontWeight: 700 }}>{inrR(h.amount_inr)}</td>
            <td>{h.item_title}</td>
            <td><span className="mono" style={{ whiteSpace: "nowrap" }}>{dayTime(h.decide_by)}</span></td>
            <td className="r">{h.days_left ?? "–"}</td>
          </>} />
        <p className="note" style={{ marginTop: 8 }}>A missed step goes to the department head; at day 60 a hold with no decision is released and recorded as a compliance miss.</p>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- accounts the rules leave to the officer
// Grey zone (one score between the cut-offs) and exposed businesses (fraud money comes in, their own money stays):
// the rules suggest nothing, so each is a decision of its own. Same list and side panel as the other groups.
const reviewKey = (r) => `rev-${r.case_id}-${r.account_id}`;
const WHY_REVIEW = {
  GREY_ZONE: "One of its two scores is between 0.40 and 0.60, so the rules suggest nothing. You decide whether the hold stays as it is.",
  EXPOSED_BUSINESS: "It receives fraud money but keeps its own money, so the rules never suggest a larger hold or a freeze. You decide whether the hold stays as it is.",
};

export function ReviewCard({ r, officer, onDone, position, onComplaint }) {
  const panel = useLoad(() => api.account(r.case_id, r.account_id).catch(() => null), [r.case_id, r.account_id]);
  const pd = panel.data;
  const rows = evidence({ action_code: "REVIEW", opened_on: r.opened_on, held_since: null }, pd, pd?.inflows, [], true);
  return (
    <article className="card dc narrowcard">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}><span className="kind">{position}</span><span className="pill p-wait">Waiting</span></div>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "8px 16px", marginBottom: 10 }}>
        <div>
          <p style={{ fontSize: 17, fontWeight: 700 }}>{accountName(r.holder_name || r.account_id)} <span className="id">{r.account_id}</span></p>
          <p className="note">{r.account_type ? `${r.account_type} account · ` : ""}<a href={`#/case/${encodeURIComponent(r.case_id)}`}>{r.label}</a> · <a href={`#/record/${encodeURIComponent(r.account_id)}`}>hold record</a></p>
        </div>
        <span style={{ alignSelf: "flex-start" }}><span className={`pill ${r.account_pattern === "GREY_ZONE" ? "p-wait" : "p-plain"}`}>{typeName(r.account_pattern)}</span></span>
      </div>
      <h4 style={{ fontSize: 19, marginBottom: 6 }}>Keep the {inrR(r.held_inr)} hold on {shortName(r.holder_name || r.account_id)} as it is?</h4>
      <p style={{ fontSize: 14, marginBottom: 12 }}>{WHY_REVIEW[r.account_pattern]}</p>
      <EvidenceTable rows={rows} score={pd?.evidence || { pass_through: r.pass_through, fraud_exposure: r.fraud_exposure, account_pattern: r.account_pattern }} />
      {(
        <Pinned><div className="acts" style={{ flexWrap: "wrap", alignItems: "center" }}>
          <Act label="Keep the hold as it is" primary officer={officer} run={(b) => api.reviewKeep(r.case_id, r.account_id, b)} onDone={onDone} />
          {!onComplaint && <a className="linkbtn" href={`#/case/${encodeURIComponent(r.case_id)}/${encodeURIComponent(r.account_id)}`}>Change it from the complaint</a>}
        </div></Pinned>
      )}
      {!officer && !SNAPSHOT && <p style={{ fontSize: 14, marginTop: 10 }}>Only a signed-in nodal officer can decide, because their name goes on it. <a href="#/signin">Sign in</a></p>}
    </article>
  );
}

export function ReviewAccounts({ data, officer, onDone, openId, setOpenId }) {
  const list = data?.review || [];
  const rowClass = useRowClass();
  const listRef = useRef(null);
  if (!list.length) return null;
  const openR = list.find((r) => reviewKey(r) === openId) || null;
  const decided = (r) => { const next = list.find((x) => reviewKey(x) !== reviewKey(r)); setOpenId(next ? reviewKey(next) : null); onDone?.(); };
  return (
    <div id="g-review" style={{ marginTop: 26, scrollMarginTop: 110 }}>
      <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="lg-review">
        <div style={{ marginBottom: 12 }}>
          <h3 id="lg-review" style={{ fontSize: 20 }}>Review an account · {list.length}</h3>
          <p className="note" style={{ fontSize: 14 }}>Held accounts the rules make no suggestion for: grey-zone accounts and exposed businesses.</p>
        </div>
        <div className="tw" ref={listRef}>
          <table className="t">
            <thead><tr><th scope="col">Account</th><th scope="col">Type</th><th scope="col" className="r">Pass-through</th><th scope="col" className="r">Fraud exposure</th><th scope="col" className="r">Held</th><th scope="col">Complaint</th><th /></tr></thead>
            <tbody>
              {list.map((r) => {
                const isOpen = openId === reviewKey(r);
                const pick = (e) => { if (!e.target.closest("button, a, input, label, select")) setOpenId(reviewKey(r)); };
                return (
                  <tr key={reviewKey(r)} className={rowClass(r.case_id, isOpen)} onClick={pick}>
                    <td><b>{shortName(r.holder_name || r.account_id)}</b></td>
                    <td>{typeName(r.account_pattern)}</td>
                    <td className="r">{n(r.pass_through).toFixed(2)}</td>
                    <td className="r">{n(r.fraud_exposure).toFixed(2)}</td>
                    <td className="r" style={{ fontWeight: 700 }}>{inrR(r.held_inr)}</td>
                    <td><ComplaintLink caseId={r.case_id} fallback={r.label} /></td>
                    <td className="r"><button className="btn btn-s" type="button" aria-expanded={isOpen}
                      onClick={() => setOpenId(isOpen ? null : reviewKey(r))}>{isOpen ? "Close" : "Open"}</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="note" style={{ marginTop: 8 }}>Grey zone: one of the two scores is between 0.40 and 0.60. Exposed business: fraud money comes in, but the account keeps its own money. Keeping the hold records your review; to change the hold, work from the complaint.</p>
        {openR && (
          <SidePanel title="Review an account" itemKey={reviewKey(openR)} onClose={() => setOpenId(null)} beside={listRef}>
            <ReviewCard key={reviewKey(openR)} r={openR} officer={officer} onDone={() => decided(openR)}
              position={`Account to review · ${list.indexOf(openR) + 1} of ${list.length}`} />
          </SidePanel>
        )}
      </section>
    </div>
  );
}