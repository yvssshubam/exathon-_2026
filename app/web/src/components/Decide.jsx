import { useState } from "react";
import { api } from "../api.js";
import { inr } from "../format.js";

const DECIDED = ["APPROVED", "EDITED", "REJECTED"];

// The officer's buttons for one of the four decisions (narrow, widen to a full freeze, unmatched notice, release).
// Nothing changes until the officer decides. The checker carries the decision out and enforces the limits.
export function labelsFor(p) {
  const held = p.current_hold_inr;
  switch (p.action_code) {
    case "PROPOSE_NARROW":
      return { yes: `Hold only ${inr(p.amount_inr)}`, no: held ? `Keep holding ${inr(held)}` : "Keep the hold", signs: true };
    case "PROPOSE_FULL_FREEZE":
      return { yes: "Freeze the whole account", no: "Keep the amount-only hold", signs: true };
    case "UNMATCHED_NOTICE":
      return { yes: "Accept the near match and work the ticket", no: "Leave it with the police" };
    case "PROPOSE_RELEASE":
      return { yes: "Release the hold", no: "Keep it on the watch" };
    default:
      return { yes: "Approve", no: "Reject" };
  }
}

export default function Decide({ p, officer, onDone, compact = false }) {
  const labels = labelsFor(p);
  const [mode, setMode] = useState(null);              // null | approve | edit | reject
  const [comment, setComment] = useState("");
  const [signed, setSigned] = useState(p.draft_text || "");
  const [amount, setAmount] = useState(p.amount_inr ?? "");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const isNarrow = p.action_code === "PROPOSE_NARROW";

  if (DECIDED.includes(p.proposal_status)) {
    const s = p.proposal_status;
    return <p className={`outcome ${s === "REJECTED" ? "rejected" : ""}`}>Decided: {s === "REJECTED" ? "kept as it was" : s === "EDITED" ? "approved with a changed amount" : "approved"}.</p>;
  }

  async function send(kind) {
    if (!officer.trim()) return setError("Type your name in the Officer box at the top first: every decision carries an officer's name.");
    setBusy(true);
    setError(null);
    try {
      if (kind === "reject") await api.reject(p.proposal_id, { officer, comment });
      else await api.approve(p.proposal_id, {
        officer,
        comment: comment || null,
        amount: kind === "edit" ? Number(amount) : null,
        signed_text: labels.signs && signed !== p.draft_text ? signed : null,
      });
      setMode(null);
      onDone?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (mode === null) {
    return (
      <>
        <div className="row">
          <button className={`btn btn-primary ${compact ? "btn-sm" : ""}`} disabled={busy}
                  onClick={() => (labels.signs ? setMode("approve") : send("approve"))}>{busy ? "Saving…" : labels.yes}</button>
          {isNarrow && <button className={`btn ${compact ? "btn-sm" : ""}`} onClick={() => setMode("edit")}>Change amount</button>}
          <button className={`btn btn-quiet ${compact ? "btn-sm" : ""}`} onClick={() => setMode("reject")}>{labels.no}</button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
      </>
    );
  }
  return (
    <form className="decide" onSubmit={(e) => { e.preventDefault(); send(mode); }}>
      {mode === "edit" && (
        <label>Hold this much instead (₹). At least the victims' money, less than what is held now.
          <input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        </label>
      )}
      {mode !== "reject" && labels.signs && (
        <label>The reasons you sign (the agent's draft: change anything you would not sign)
          <textarea rows={7} value={signed} onChange={(e) => setSigned(e.target.value)} required />
        </label>
      )}
      <label>
        {mode === "reject" ? "Why not? (goes into the audit log)" : mode === "edit" ? "Why this amount? (required)" : "Note (optional)"}
        <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} required={mode !== "approve"} />
      </label>
      {mode !== "reject" && labels.signs && p.draft_text && signed !== p.draft_text && (
        <p className="note">You changed the agent's draft; both are kept on the hold record. <button type="button" className="linkbtn" onClick={() => setSigned(p.draft_text)}>Restore the draft</button></p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="row">
        <button className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : mode === "reject" ? labels.no : mode === "edit" ? "Hold this amount" : labels.yes}</button>
        <button type="button" className="btn btn-quiet" onClick={() => { setMode(null); setError(null); }}>Cancel</button>
      </div>
    </form>
  );
}