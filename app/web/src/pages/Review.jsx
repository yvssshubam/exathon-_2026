import { useState } from "react";
import { api } from "../api.js";
import { clock, inr, when } from "../format.js";
import { useLoad } from "../hooks.js";
import { Failed, Loading, NightBadge } from "../components/bits.jsx";
import Proposal from "../components/Proposal.jsx";

// The officer's morning: what is waiting for a decision (left), what the agent did on its own (right).
export default function Review({ officer }) {
  const { data, error, loading, reload } = useLoad(() => api.inbox(), []);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);

  async function run(label, fn) {
    setBusy(label);
    setNote(null);
    try {
      const r = await fn();
      if (r?.worked) setNote(`Worked ${r.worked.length} ticket${r.worked.length === 1 ? "" : "s"} in ${r.seconds.toFixed(1)} s.`);
      reload();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(null);
    }
  }

  if (loading && !data) return <Loading what="Loading the morning review" />;
  if (error) return <Failed error={error} retry={reload} />;
  const byCase = {};
  for (const w of data.waiting) (byCase[w.case_id] ||= []).push(w);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>Morning review</h1>
          <p className="lede">The agent worked every ticket the moment it landed and placed only the holds the rules require. The judgment calls are yours.</p>
        </div>
        <div className="row">
          {data.queued > 0 && (
            <button className="btn btn-primary" disabled={!!busy} onClick={() => run("queue", () => api.workQueue())}>
              {busy === "queue" ? "Working the queue…" : `Work the ${data.queued} queued ticket${data.queued === 1 ? "" : "s"}`}
            </button>
          )}
          <button className="btn" disabled={!!busy} onClick={() => run("reset", () => api.reset())}>
            {busy === "reset" ? "Resetting…" : "Reset the demo"}
          </button>
        </div>
      </header>
      {note && <p className="note" role="status">{note}</p>}

      <div className="review">
        <section className="waiting" aria-labelledby="waiting-h">
          <h2 id="waiting-h">Waiting for you <span className="count">{data.waiting.length}</span></h2>
          {data.waiting.length === 0 && <p className="empty">Nothing is waiting. New judgment calls appear here as the agent works tickets.</p>}
          {Object.entries(byCase).map(([caseId, list]) => (
            <div key={caseId} className="case-group">
              <h3><a href={`#/case/${caseId}`}>{caseId}</a></h3>
              {list.map((a) => (
                <Proposal key={a.account_id + a.action_code} caseId={caseId} action={a} officer={officer} onDone={reload} />
              ))}
            </div>
          ))}
          {data.kyc_to_send.length > 0 && (
            <p className="quiet kyc-note">
              {data.kyc_to_send.length} KYC packet{data.kyc_to_send.length === 1 ? " is" : "s are"} drafted and ready to send (due within a week).
              Open the case's paperwork to send them.
            </p>
          )}
        </section>

        <section className="done" aria-labelledby="done-h">
          <h2 id="done-h">Done automatically <span className="count">{data.done.length}</span></h2>
          {data.done.length === 0 && <p className="empty">No tickets worked yet. Work the queue to replay the night.</p>}
          <ol className="done-list">
            {data.done.map((d) => (
              <li key={d.case_id}>
                <a href={`#/case/${d.case_id}`} className="done-item">
                  <span className="done-time"><b>{clock(d.received_at)}</b><NightBadge on={d.after_hours} /></span>
                  <span className="done-body">
                    <span className="done-title">{d.case_id}: {inr(d.amount_inr)} reported</span>
                    <span className="done-facts">
                      {d.run_status === "SENT_BACK" ? "Notice sent back for correction; nothing frozen." :
                       d.run_status === "CHECK_FAILED" ? "The checker stopped it; nothing held. Needs you." :
                       d.run_status === "FAILED" ? "The run failed; see the agent log." :
                       `${d.holds} hold${d.holds === 1 ? "" : "s"} (${inr(d.held_inr)}), ${d.exits} exit${d.exits === 1 ? "" : "s"}, ${d.onward} onward entr${d.onward === 1 ? "y" : "ies"}; holds landed ${clock(d.sim_holds_at)}`}
                    </span>
                    {d.waiting > 0 && <span className="badge badge-waiting">{d.waiting} waiting</span>}
                  </span>
                </a>
              </li>
            ))}
          </ol>
          {data.done[0] && <p className="quiet">Latest run took {Number(data.done[0].wall_seconds || 0).toFixed(1)} s on this laptop, {when(data.done[0].received_at)} on the simulated clock.</p>}
        </section>
      </div>
    </>
  );
}