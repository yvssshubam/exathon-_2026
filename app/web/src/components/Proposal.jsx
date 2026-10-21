import { useState } from "react";
import { api } from "../api.js";
import { inr } from "../format.js";

// One judgment call the agent prepared. Approve, edit (narrowing only) or reject; nothing changes until then.
export default function Proposal({ caseId, action, officer, onDone, showCase = false }) {
  const [mode, setMode] = useState(null);              // null | "approve" | "edit" | "reject"
  const [text, setText] = useState("");
  const [amount, setAmount] = useState(action.amount_inr ?? "");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const decided = !["PROPOSED", "WAITING"].includes(action.action_status);
  const isFreeze = action.action_code === "PROPOSE_FULL_FREEZE";
  const canEdit = action.action_code === "PROPOSE_NARROW";

  async function submit(e) {
    e.preventDefault();
    if (!officer.trim()) return setError("Enter your name in the sidebar first: every decision carries an officer's name.");
    setBusy(true);
    setError(null);
    try {
      if (mode === "reject") {
        await api.reject(caseId, action.account_id, { action_code: action.action_code, officer, reason: text });
      } else {
        await api.approve(caseId, action.account_id, {
          action_code: action.action_code, officer, comment: text || null,
          amount: mode === "edit" ? Number(amount) : null,
        });
      }
      setMode(null);
      onDone?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className={`proposal ${isFreeze ? "proposal-freeze" : ""} ${decided ? "decided" : ""}`}>
      <header>
        <h4>{action.headline}</h4>
        <p className="quiet">
          {action.name || action.account_id}{showCase && <> in <a href={`#/case/${caseId}/${encodeURIComponent(action.account_id)}`}>{caseId}</a></>}
          {action.current_hold_inr != null && <>, held now {inr(action.current_hold_inr)}</>}
        </p>
      </header>
      <ul className="reasons">{action.explanation.map((s, i) => <li key={i}>{s}</li>)}</ul>
      {action.legal_basis && <p className="basis">{action.legal_basis}</p>}
      {decided ? (
        <p className={`outcome outcome-${action.action_status.toLowerCase()}`}>Decided: {action.action_status.toLowerCase()}</p>
      ) : mode === null ? (
        <div className="row">
          <button className="btn btn-primary" onClick={() => setMode("approve")}>{isFreeze ? "Freeze the account" : action.action_code === "PROPOSE_NARROW" ? `Narrow to ${inr(action.amount_inr)}` : "Confirm the hold"}</button>
          {canEdit && <button className="btn" onClick={() => setMode("edit")}>Change amount</button>}
          <button className="btn" onClick={() => setMode("reject")}>Reject</button>
        </div>
      ) : (
        <form onSubmit={submit} className="decide">
          {mode === "edit" && (
            <label>Narrow the hold to (₹)
              <input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required />
            </label>
          )}
          <label>
            {mode === "reject" ? "Why are you rejecting it? (required)" : isFreeze ? "The material behind the freeze (required)" : mode === "edit" ? "Why this amount? (required)" : "Comment (optional)"}
            <textarea rows="2" value={text} onChange={(e) => setText(e.target.value)} required={mode !== "approve" || isFreeze} />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="row">
            <button className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : mode === "reject" ? "Reject" : "Approve"}</button>
            <button type="button" className="btn" onClick={() => { setMode(null); setError(null); }}>Cancel</button>
          </div>
        </form>
      )}
      {mode === null && error && <p className="error" role="alert">{error}</p>}
    </article>
  );
}