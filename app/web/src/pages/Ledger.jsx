import { api } from "../api.js";
import { Failed, Loading, Stmt } from "../components/bits.jsx";
import { accountName, clock, inr, plural, victim, when } from "../format.js";
import { useLoad } from "../hooks.js";

// One card per account. Each line is a hold in force (or released); a hold that was replaced is not a line of its
// own: it is folded into the hold that took over, so the history stays visible without struck-out rows.
function holdWhose(h) {
  if (h.hold_kind === "MARKER") return "Watching for new money";
  if (h.hold_tag === "NOT_LINKED") return `${inr(h.amount_inr)} caught while watching, not linked to a victim yet`;
  const money = victim(h.hold_tag) ? `${victim(h.hold_tag)}'s money` : "the victims' money";
  return h.hold_kind === "ACCOUNT_FREEZE" ? `Whole account frozen (${inr(h.amount_inr)})` : `${inr(h.amount_inr)} of ${money}`;
}

function byWhom(h) {
  return h.placed_by === "agent" ? "the system" : h.placed_by;
}

function history(h, all) {
  const before = all.filter((x) => x.replaced_by != null && Number(x.replaced_by) === Number(h.hold_id));
  return before.map((b) => {
    if (b.hold_tag === "NOT_LINKED")
      return `First held at ${clock(b.placed_at)} while the account was being watched, before anyone complained. Linked to ${victim(h.hold_tag) || "the victim"} when the ${h.label} arrived.`;
    if (Number(b.amount_inr) > Number(h.amount_inr))
      return `Was ${inr(b.amount_inr)} from ${clock(b.placed_at)}; reduced to ${inr(h.amount_inr)} by ${byWhom(h)}.`;
    if (Number(b.amount_inr) < Number(h.amount_inr))
      return `Was ${inr(b.amount_inr)} from ${clock(b.placed_at)}; raised to ${inr(h.amount_inr)} by ${byWhom(h)}.`;
    return `Replaced an earlier hold placed at ${clock(b.placed_at)}.`;
  });
}

export function Holds() {
  const { data, error, loading, reload } = useLoad(() => api.holds(), []);
  if (loading && !data) return <div className="wrap page"><Loading what="Loading the hold ledger" /></div>;
  if (error) return <div className="wrap page"><Failed error={error} retry={reload} /></div>;

  const accounts = [];
  for (const h of data) {
    let a = accounts.find((x) => x.id === h.account_id);
    if (!a) accounts.push(a = { id: h.account_id, name: accountName(h.holder_name, h.account_id), holds: [] });
    a.holds.push(h);
  }
  for (const a of accounts) {
    a.shown = a.holds.filter((h) => h.hold_status !== "REPLACED")
      .sort((x, y) => String(x.placed_at).localeCompare(String(y.placed_at)));
    a.held = a.holds.filter((h) => h.hold_status === "ACTIVE" && h.hold_kind !== "MARKER")
      .reduce((t, h) => t + Number(h.amount_inr || 0), 0);
  }
  accounts.sort((x, y) => y.held - x.held);
  const total = accounts.reduce((t, a) => t + a.held, 0);

  return (
    <main id="main" className="wrap page">
      <div className="pagehead">
        <div>
          <h1>Holds</h1>
          <p className="lede">Money held on this bank's accounts, account by account: whose money it is, when it was held and for which complaint. The system holds only what the rules require; a smaller or wider hold carries an officer's name.</p>
          {accounts.length > 0 && <p className="holdtotal"><b>{inr(total)}</b> held now across {plural(accounts.filter((a) => a.held > 0).length, "account")}.</p>}
        </div>
      </div>
      {accounts.length === 0 ? <p className="empty">No holds yet.</p> : (
        <div className="holdcards">
          {accounts.map((a) => (
            <section key={a.id} className="card holdcard">
              <header>
                <h2><a href={`#/record/${encodeURIComponent(a.id)}`}>{a.name}</a></h2>
                <span className="holdsum">{a.held > 0 ? <><b>{inr(a.held)}</b> held now</> : "Nothing held now"}</span>
              </header>
              <ul>
                {a.shown.map((h) => (
                  <li key={h.hold_id} className={h.hold_status === "RELEASED" ? "released" : ""}>
                    <div className="holdline">
                      <span className="whose">{holdWhose(h)}</span>
                      {h.hold_status === "RELEASED" && <span className="pill pill-plain">released</span>}
                    </div>
                    <div className="note">
                      Held {when(h.placed_at)} by {byWhom(h)} · <a href={`#/case/${h.case_id}`}>{h.label || h.case_id}</a> <Stmt ids={h.stmt_id} />
                    </div>
                    {history(h, a.holds).map((t, i) => <div key={i} className="note was">{t}</div>)}
                  </li>
                ))}
              </ul>
            </section>
          ))}
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
          <p className="lede">Every step and every decision, with the Exasol statement behind it, in simulated time. Append-only: the maker and checker users can add rows, nobody but the admin can change one.</p>
        </div>
      </div>
      {data.length === 0 ? <p className="empty">Nothing logged yet.</p> : (
        <div className="table-wrap">
          <table className="table compact wide">
            <thead><tr><th>When</th><th>Who</th><th>What</th><th>Complaint</th><th>Detail</th><th>Statement</th></tr></thead>
            <tbody>
              {data.map((e) => (
                <tr key={e.event_id}>
                  <td>{when(e.sim_at || e.event_at)}</td><td>{e.actor}</td><td>{e.event_name.replaceAll("_", " ")}</td>
                  <td>{e.case_id ? <a href={`#/case/${e.case_id}`}>{e.label || e.case_id}</a> : ""}</td>
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