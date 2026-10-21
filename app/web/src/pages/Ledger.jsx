import { api } from "../api.js";
import { inr, when } from "../format.js";
import { useLoad } from "../hooks.js";
import { Failed, Loading, Stmt } from "../components/bits.jsx";

export function Holds() {
  const { data, error, loading, reload } = useLoad(() => api.holds(), []);
  if (loading && !data) return <Loading what="Loading the hold ledger" />;
  if (error) return <Failed error={error} retry={reload} />;
  return (
    <>
      <header className="page-head"><div><h1>Hold ledger</h1><p className="lede">Every hold on this bank's accounts, who placed it and why. The agent can only place the holds the rules require; anything wider carries an officer's name.</p></div></header>
      {data.length === 0 ? <p className="empty">No holds yet.</p> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Account</th><th>Case</th><th>Kind</th><th className="num">Amount</th><th>Placed</th><th>By</th><th>Status</th></tr></thead>
            <tbody>
              {data.map((h) => (
                <tr key={h.hold_id} className={h.hold_status === "REPLACED" ? "struck" : ""}>
                  <td><a href={`#/case/${h.case_id}/${encodeURIComponent(h.account_id)}`}>{h.holder_name || h.account_id}</a></td>
                  <td>{h.case_id}</td>
                  <td>{h.hold_kind === "LIEN" ? "Amount only" : h.hold_kind === "ACCOUNT_FREEZE" ? "Whole account" : "Marker"}</td>
                  <td className="num">{h.hold_kind === "MARKER" ? "–" : inr(h.amount_inr)}</td>
                  <td>{when(h.placed_at)}</td>
                  <td>{h.placed_by}</td>
                  <td>{h.hold_status.toLowerCase()} <Stmt ids={h.stmt_id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function Audit() {
  const { data, error, loading, reload } = useLoad(() => api.audit(), []);
  if (loading && !data) return <Loading what="Loading the audit log" />;
  if (error) return <Failed error={error} retry={reload} />;
  return (
    <>
      <header className="page-head"><div><h1>Audit log</h1><p className="lede">Every step and every decision, with the Exasol session and statement behind it.</p></div></header>
      <div className="table-wrap">
        <table className="table compact">
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>Case</th><th>Detail</th><th>Statement</th></tr></thead>
          <tbody>
            {data.map((e) => (
              <tr key={e.event_id}><td>{when(e.event_at)}</td><td>{e.actor}</td><td>{e.event_name.replaceAll("_", " ")}</td><td>{e.case_id || ""}</td><td className="detail">{e.detail}</td><td><Stmt ids={e.stmt_id} /></td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}