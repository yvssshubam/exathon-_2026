import { api } from "../api.js";
import { Failed, Loading, Stmt } from "../components/bits.jsx";
import { clock, inr, when } from "../format.js";
import { useLoad } from "../hooks.js";

// The hold record (Module 7): one account's holds at this bank, from the records. The notice, the path the money
// took, the lowest-balance steps, the rule, the proposals, who decided, every change, the split between victims,
// what the holder was told, and the Exasol statement behind each number. What the bank files or shows a court.
const KIND = { LIEN: "Amount only", ACCOUNT_FREEZE: "Whole account", MARKER: "Marker for new money" };
const rs = (s) => String(s || "").replace(/Rs (?=\d)/g, "₹");

export default function Record({ accountId }) {
  const { data, error, loading, reload } = useLoad(() => api.record(accountId), [accountId]);
  if (loading && !data) return <div className="wrap page"><Loading what="Loading the hold record" /></div>;
  if (error) return <div className="wrap page"><Failed error={error} retry={reload} /></div>;
  const a = data.account;
  const labels = Object.fromEntries(data.cases.map((c) => [c.case_id, c.label]));
  return (
    <main id="main" className="wrap page record">
      <nav className="crumb" aria-label="Breadcrumb"><a href="#/review">Review</a> / <a href="#/holds">Holds</a> / Hold record</nav>
      <div className="pagehead">
        <div>
          <h1>Hold record: {a.holder_name || a.account_id}</h1>
          <div className="meta">
            <span className="mono">{a.account_id}</span><span>{a.bank_name}</span>
            {a.account_type && <span>{a.account_type} account, {a.holder_type}</span>}
            {a.opened_on && <span>opened {String(a.opened_on).slice(0, 10)}</span>}
          </div>
        </div>
        <div className="row">
          <span className="pill pill-held pill-lg">Held now {inr(data.held_now_inr)}</span>
          <a className="btn btn-sm" href={api.recordTextUrl(accountId)} download={`hold-record-${accountId}.txt`}>Download as text</a>
        </div>
      </div>

      {data.cases.map((c) => <CaseBlock key={c.case_id} c={c} />)}


      <section className="recsec">
        <h2>Holds, in order</h2>
        <div className="table-wrap">
          <table className="table compact wide">
            <thead><tr><th>Hold</th><th>Complaint</th><th>Kind</th><th className="num">Amount</th><th>Tag</th><th>Placed</th><th>By</th><th>Status</th><th>Statement</th></tr></thead>
            <tbody>
              {data.holds.map((h) => (
                <tr key={h.hold_id} className={h.hold_status !== "ACTIVE" ? "struck" : ""}>
                  <td className="mono">PH-H{h.hold_id}</td>
                  <td><a href={`#/case/${h.case_id}`}>{labels[h.case_id] || h.case_id}</a></td>
                  <td>{KIND[h.hold_kind] || h.hold_kind}</td>
                  <td className="num">{h.hold_kind === "MARKER" ? "–" : inr(h.amount_inr)}</td>
                  <td>{h.hold_tag === "NOT_LINKED" ? "not linked to a victim yet" : h.hold_tag || "–"}</td>
                  <td>{when(h.placed_at)}</td>
                  <td>{h.placed_by}</td>
                  <td>{h.hold_status.toLowerCase()}{h.replaced_by ? ` → PH-H${h.replaced_by}` : ""}</td>
                  <td><Stmt ids={h.stmt_id} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {data.splits.length > 0 && (
        <section className="recsec">
          <h2>Split between victims</h2>
          <p className="note">A record for the refund stage: it moves no money. A new version is written whenever a new victim's ticket reaches the account; old versions are kept.</p>
          <div className="table-wrap">
            <table className="table compact wide">
              <thead><tr><th>Version</th><th>Victim</th><th className="num">Their money here</th><th className="num">Share of the held amount</th><th>Why</th><th>From</th><th>Statement</th></tr></thead>
              <tbody>
                {data.splits.map((s) => (
                  <tr key={`${s.version_no}-${s.victim_tag}`} className={s.superseded_at ? "struck" : ""}>
                    <td>v{s.version_no}</td><td>{s.victim_tag}</td><td className="num">{inr(s.victim_inr)}</td>
                    <td className="num">{inr(s.share_of_held_inr)} of {inr(s.held_inr)}</td>
                    <td>{s.trigger_kind?.replaceAll("_", " ")}</td><td>{when(s.valid_from)}</td><td><Stmt ids={s.stmt_id} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {(data.watch_list.length > 0 || data.watch_events.length > 0) && (
        <section className="recsec">
          <h2>Since the first pass</h2>
          {data.watch_list.map((w) => (
            <p key={w.case_id + w.added_at} className="note">On the watch for the {labels[w.case_id] || w.case_id} from {when(w.added_at)} ({w.first_pass?.toLowerCase().replace("_", "-")}): {rs(w.added_why)}{w.left_at ? `; left ${when(w.left_at)} (${w.left_why})` : ""}.</p>
          ))}
          <ul className="watchlist">
            {data.watch_events.map((e, i) => <li key={i}><span className="at">{clock(e.event_at)}</span><span>{rs(e.sentence).replace(/^\d{2}:\d{2}: /, "")} <Stmt ids={e.stmt_id} /></span></li>)}
          </ul>
        </section>
      )}

      {data.holder_messages.length > 0 && (
        <section className="recsec">
          <h2>What the holder was told</h2>
          <p className="note">Only the authority, the notice and the amount: never the evidence.</p>
          <ul className="history">{data.holder_messages.map((m, i) => <li key={i}>{when(m.written_at)}: {rs(m.message_text)}</li>)}</ul>
        </section>
      )}

      {data.audit.length > 0 && (
        <details className="recsec">
          <summary>Audit log for this account ({data.audit.length})</summary>
          <table className="table compact wide">
            <tbody>
              {data.audit.map((e, i) => (
                <tr key={i}><td>{when(e.sim_at || e.event_at)}</td><td>{e.actor}</td><td>{e.event_name.replaceAll("_", " ")}</td><td className="ev">{e.detail}</td><td><Stmt ids={e.stmt_id} /></td></tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </main>
  );
}

function CaseBlock({ c }) {
  const n = c.notice;
  return (
    <section className="recsec card pad">
      <h2>The <a href={`#/case/${c.case_id}`}>{c.label}</a> <span className="muted">· from {n.complaint_source}, acknowledgement {n.ack_no}</span></h2>
      <dl className="recdl">
        <dt>Notice</dt><dd>{n.ordering_reference || "–"} from {n.ordering_authority || "–"}, {n.notice_legal_basis || "no legal basis given"}{n.fir_reference ? `; ${n.fir_reference}` : ""}</dd>
        <dt>Ticket names</dt><dd><span className="mono">UTR {n.utr}</span>, {inr(n.amount_inr)}, paid {when(n.txn_at)}, reached the bank {when(n.reported_at)}</dd>
        <dt>Matched</dt><dd>{n.match_status?.toLowerCase().replace("_", " ")} {n.matched_txn_id ? `(${n.matched_txn_id}, ${inr(n.matched_amount_inr)} at ${clock(n.matched_at)})` : ""} <Stmt ids={n.intake_stmt_id} /></dd>
        {c.trail_path && <><dt>Path the money took</dt><dd className="mono">{c.trail_path}</dd></>}
      </dl>
      {c.libr_steps.length > 0 && (
        <>
          <h3>Lowest-balance calculation</h3>
          <p className="note">The account spends its own money first; the victim's money is what is left once the balance falls below it. Replayed in Exasol, one row per payment.</p>
          <div className="table-wrap">
            <table className="table compact wide">
              <thead><tr><th>When</th><th>What</th><th className="num">Amount</th><th className="num">Balance</th><th className="num">Victim's money in it</th><th>Statement</th></tr></thead>
              <tbody>
                {c.libr_steps.map((s, i) => (
                  <tr key={i}><td>{clock(s.at)}</td><td>{s.what}</td><td className="num">{s.amount_inr == null ? "" : inr(s.amount_inr)}</td>
                    <td className="num">{inr(s.balance_inr)}</td><td className="num victim">{inr(s.victim_inr)}</td><td><Stmt ids={s.stmt_id} /></td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {c.rules.length > 0 && (
        <>
          <h3>What the rules allowed</h3>
          <ul className="history">
            {c.rules.map((r, i) => <li key={i}>{r.action_code.replaceAll("_", " ").toLowerCase()} ({r.act_mode.toLowerCase()}): {inr(r.amount_inr)}, {r.reason_code?.replaceAll("_", " ").toLowerCase()}. {r.legal_basis} <Stmt ids={r.stmt_id} /></li>)}
          </ul>
        </>
      )}
      {c.proposals.length > 0 && (
        <>
          <h3>Proposals and the checker</h3>
          <ul className="history">
            {c.proposals.map((p) => (
              <li key={p.proposal_id}>#{p.proposal_id} {p.action_code.replaceAll("_", " ").toLowerCase()} {p.amount_inr ? inr(p.amount_inr) : ""} by {p.proposed_by} at {clock(p.proposed_at)} → <b>{p.proposal_status.toLowerCase()}</b>{p.checker_note ? ` (${p.checker_note})` : ""} <Stmt ids={p.stmt_id} /></li>
            ))}
          </ul>
        </>
      )}
      {c.decisions.length > 0 && (
        <>
          <h3>Who decided</h3>
          {c.decisions.map((d) => (
            <div key={d.decision_id} className="recdecision">
              <p><b>{d.decided_by}</b> {d.decision_outcome.toLowerCase()} the {d.action_code.replaceAll("_", " ").toLowerCase()} at {when(d.decided_at)}{d.amount_inr != null ? `, ${inr(d.amount_inr)}` : ""}{d.reason_text ? `: "${d.reason_text}"` : ""}. <span className="stmt">stmts {d.statement_ids}</span></p>
              {d.signed_text && <p className="signed">{rs(d.signed_text)}</p>}
              {d.signed_text && d.draft_text && d.signed_text !== d.draft_text && <p className="note">The officer edited the agent's draft before signing; the draft is kept.</p>}
            </div>
          ))}
        </>
      )}
    </section>
  );
}