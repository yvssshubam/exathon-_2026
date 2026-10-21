import { useState } from "react";
import { api } from "../api.js";
import { inr } from "../format.js";

const DECIDED = ["APPROVED", "EDITED", "REJECTED"];

// The officer's buttons for one judgment call. `proposals` is every ticket's proposal for the same account and
// action (one approval covers them all). Nothing changes until the officer decides; the rules are enforced by the API.
export default function Decide({ proposals, officer, onDone, compact = false }) {
  const first = proposals[0];
  const code = first.action_code;
  const [mode, setMode] = useState(null);              // null | approve | edit | reject
  const [text, setText] = useState("");
  const [amount, setAmount] = useState(first.amount_inr ?? "");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const decided = proposals.every((p) => DECIDED.includes(p.action_status));
  const isFreeze = code === "PROPOSE_FULL_FREEZE";
  const isNarrow = code === "PROPOSE_NARROW";
  const empty = isFreeze && Number(first.balance_now_inr || 0) === 0;

  const labels = isNarrow
    ? { yes: `Hold only ${inr(first.amount_inr)}`, no: `Keep holding ${inr(first.current_hold_inr)}` }
    : isFreeze
      ? { yes: empty ? "Watch for new money" : "Freeze the account", no: "Don't freeze" }
      : { yes: "Confirm the hold", no: "Don't confirm" };

  async function send(kind) {
    if (!officer.trim()) return setError("Type your name in the Officer box at the top first: every decision carries an officer's name.");
    setBusy(true);
    setError(null);
    try {
      for (const p of proposals.filter((x) => !DECIDED.includes(x.action_status))) {
        if (kind === "reject") await api.reject(p.case_id, p.account_id, { action_code: code, officer, reason: text });
        else await api.approve(p.case_id, p.account_id, { action_code: code, officer, comment: text || null, amount: kind === "edit" ? Number(amount) : null });
      }
      setMode(null);
      onDone?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (decided) {
    const s = first.action_status;
    return <p className={`outcome ${s === "REJECTED" ? "rejected" : ""}`}>Decided: {s === "REJECTED" ? "kept as it was" : s === "EDITED" ? "approved with a changed amount" : "approved"}.</p>;
  }
  if (mode === null) {
    return (
      <>
        <div className="row">
          <button className={`btn btn-primary ${compact ? "btn-sm" : ""}`} onClick={() => (isFreeze ? setMode("approve") : send("approve"))} disabled={busy}>{busy ? "Saving…" : labels.yes}</button>
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
        <label>Hold this much instead (₹)
          <input type="number" min="0" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        </label>
      )}
      <label>
        {mode === "reject" ? "Why? (goes into the audit log)" : isFreeze ? "The evidence behind the freeze (required: courts ask for it)" : "Why this amount?"}
        <textarea rows="2" value={text} onChange={(e) => setText(e.target.value)} required />
      </label>
      {proposals.length > 1 && <p className="note">This decision covers all {proposals.length} tickets that name this account.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="row">
        <button className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : mode === "reject" ? labels.no : mode === "edit" ? "Hold this amount" : labels.yes}</button>
        <button type="button" className="btn btn-quiet" onClick={() => { setMode(null); setError(null); }}>Cancel</button>
      </div>
    </form>
  );
}