import { api } from "../api.js";
import { Failed, Loading, Stmt } from "../components/bits.jsx";
import { inr, when } from "../format.js";
import { useLoad } from "../hooks.js";

const KIND = { LIEN: "Amount only", ACCOUNT_FREEZE: "Whole account", MARKER: "Marker for new money" };

export function Holds() {
  const { data, error, loading, reload } = useLoad(() => api.holds(), []);
  if (loading && !data) return <div className="wrap page"><Loading what="Loading the hold ledger" /></div>;
  if (error) return <div className="wrap page"><Failed error={error} retry={reload} /></div>;
  return (
    <main id="main" className="wrap page">
      <div className="pagehead">
        <div>
          <h1>Hold ledger</h1>
          <p className="lede">Every hold on this bank's accounts, who placed it and why. The agent can only place the holds the rules require; anything wider carries an officer's name.</p>
        </div>
      </div>
      {data.length === 0 ? <p className="empty">No holds yet.</p> : (
        <div className="table-wrap">
          <table className="table wide">
            <thead><tr><th>Account</th><th>Ticket</th><th>Kind</th><th className="num">Amount</th><th>Placed</th><th>By</th><th>Status</th></tr></thead>
            <tbody>
              {data.map((h) => (
                <tr key={h.hold_id} className={h.hold_status === "REPLACED" ? "struck" : ""}>
                  <td className="name"><a href={`#/case/${h.case_id}/${encodeURIComponent(h.account_id)}`}>{h.holder_name || h.account_id}</a></td>
                  <td><a href={`#/case/${h.case_id}`}>{h.case_id}</a></td>
                  <td>{KIND[h.hold_kind] || h.hold_kind}</td>
                  <td className="num">{h.hold_kind === "MARKER" ? "–" : inr(h.amount_inr)}</td>
                  <td>{when(h.placed_at)}</td>
                  <td>{h.placed_by === "agent" ? <span className="pill pill-held">agent</span> : h.placed_by}</td>
                  <td>{h.hold_status.toLowerCase()} <Stmt ids={h.stmt_id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}

export function Audit() {
  const { data, error, loading, reload } = useLoad(() => api.audit(), []);
  if (loading && !data) return <div className="wrap page"><Loading what="Loading the audit log" /></div>;
  if (error) return <div className="wrap page"><Failed error={error} retry={reload} /></div>;
  return (
    <main id="main" className="wrap page">
      <div className="pagehead">
        <div>
          <h1>Audit log</h1>
          <p className="lede">Every step and every decision, with the Exasol statement behind it.</p>
        </div>
      </div>
      {data.length === 0 ? <p className="empty">Nothing logged yet.</p> : (
        <div className="table-wrap">
          <table className="table compact wide">
            <thead><tr><th>When</th><th>Who</th><th>What</th><th>Ticket</th><th>Detail</th><th>Statement</th></tr></thead>
            <tbody>
              {data.map((e) => (
                <tr key={e.event_id}>
                  <td>{when(e.event_at)}</td><td>{e.actor}</td><td>{e.event_name.replaceAll("_", " ")}</td>
                  <td>{e.case_id ? <a href={`#/case/${e.case_id}`}>{e.case_id}</a> : ""}</td>
                  <td className="ev">{e.detail}</td><td><Stmt ids={e.stmt_id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}