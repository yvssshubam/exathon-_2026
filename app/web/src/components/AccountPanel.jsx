import { api } from "../api.js";
import { inr, when } from "../format.js";
import { useLoad } from "../hooks.js";
import { Failed, Loading, MoneyBar, Stmt } from "./bits.jsx";
import Proposal from "./Proposal.jsx";

// Action first, then the split of the money, then the evidence behind the reasons.
export default function AccountPanel({ caseId, accountId, officer, onClose, onChanged }) {
  const { data, error, loading, reload } = useLoad(() => api.account(caseId, accountId), [caseId, accountId]);
  if (loading && !data) return <aside className="panel"><Loading /></aside>;
  if (error) return <aside className="panel"><Failed error={error} retry={reload} /></aside>;
  const { account, split, actions, holds, decisions, reasons, signals, evidence } = data;
  const auto = actions.filter((a) => a.act_mode === "AUTOMATIC");
  const officerCalls = actions.filter((a) => a.act_mode === "OFFICER");
  const isExit = accountId.startsWith("EXIT:");
  return (
    <aside className="panel" aria-label={`Account ${accountId}`}>
      <header className="panel-head">
        <div>
          <h3>{account?.holder_name || accountId}</h3>
          <p className="quiet">{isExit ? "Money left banking here" : `${account?.bank_name}, ${account?.account_type} account opened ${account?.opened_on}`}</p>
        </div>
        <button className="btn btn-ghost" onClick={onClose} aria-label="Close the account panel">Close</button>
      </header>

      {auto.map((a) => (
        <section key={a.action_code} className="done-action">
          <h4>{a.headline}</h4>
          <ul className="reasons">{a.explanation.map((s, i) => <li key={i}>{s}</li>)}</ul>
          <p className="basis">{a.legal_basis} <Stmt ids={a.stmt_id} /></p>
        </section>
      ))}
      {officerCalls.map((a) => (
        <Proposal key={a.action_code} caseId={caseId} action={a} officer={officer} onDone={() => { reload(); onChanged?.(); }} />
      ))}

      {!isExit && (
        <section>
          <h4 className="sub">Whose money is in it at the hold time</h4>
          <MoneyBar total={split.balance_now_inr} parts={[
            { key: "victim", label: "This victim's", value: split.victim_amount_inr },
            { key: "others", label: "Other victims'", value: split.other_victims_inr },
            { key: "own", label: "The holder's own", value: split.own_funds_inr },
          ]} caption={<>Lowest-balance rule, as of {when(split.as_of)} <Stmt ids={split.stmt_id} /></>} />
        </section>
      )}

      {holds.length > 0 && (
        <section>
          <h4 className="sub">Holds on this account</h4>
          <table className="table compact">
            <thead><tr><th>Kind</th><th className="num">Amount</th><th>Placed</th><th>By</th><th>Status</th></tr></thead>
            <tbody>
              {holds.map((h) => (
                <tr key={h.hold_id} className={h.hold_status === "REPLACED" ? "struck" : ""}>
                  <td>{h.hold_kind === "LIEN" ? "Amount only" : h.hold_kind === "ACCOUNT_FREEZE" ? "Whole account" : "Marker"}</td>
                  <td className="num">{h.hold_kind === "MARKER" ? "–" : inr(h.amount_inr)}</td>
                  <td>{when(h.placed_at)}</td>
                  <td>{h.placed_by}</td>
                  <td>{h.hold_status.toLowerCase()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {reasons.length > 0 && (
        <section>
          <h4 className="sub">What the account's last 90 days show</h4>
          <ul className="evidence">{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <details>
            <summary>All eight signals <Stmt ids={evidence?.stmt_id} /></summary>
            <table className="table compact">
              <thead><tr><th>Signal</th><th className="num">Value</th><th className="num">Weight</th><th className="num">Pull towards pass-through</th></tr></thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.signal_name}>
                    <td>{s.sentence}</td>
                    <td className="num">{s.signal_value == null ? "–" : Number(s.signal_value).toFixed(2)}</td>
                    <td className="num">{Number(s.weight).toFixed(2)}</td>
                    <td className="num">{Number(s.contribution || 0).toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="quiet">The signals are an input to the rules, not a verdict. Weights are hand-set for the demo and not validated on real data.</p>
          </details>
        </section>
      )}

      {decisions.length > 0 && (
        <section>
          <h4 className="sub">Officer decisions</h4>
          <ul className="plain">
            {decisions.map((d) => (
              <li key={d.decision_id}>{when(d.decided_at)}: {d.decided_by} {d.decision_outcome.toLowerCase()} {d.action_code.replace("PROPOSE_", "").replace("_", " ").toLowerCase()}{d.amount_inr != null ? ` at ${inr(d.amount_inr)}` : ""}{d.reason_text ? `: “${d.reason_text}”` : ""}</li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}