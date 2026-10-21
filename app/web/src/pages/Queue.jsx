import { useState } from "react";
import { api } from "../api.js";
import { Failed, Loading } from "../components/bits.jsx";
import { clock, inr, plural } from "../format.js";
import { useLoad } from "../hooks.js";

const STATUS = {
  QUEUED: ["Waiting for the agent", "pill-plain"],
  WORKING: ["Agent working", "pill-plain"],
  DONE: ["Agent's part done", "pill-held"],
  SENT_BACK: ["Sent back to the police", "pill-other"],
  FAILED: ["Agent run failed", "pill-gone"],
};

// The simulated CFCFRMS queue at the bank, in the order tickets landed.
export default function Queue({ modelOn }) {
  const { data, error, loading, reload } = useLoad(() => api.queue(), []);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);

  async function work(caseId, mode) {
    setBusy(caseId + mode);
    setNote(null);
    try {
      const r = caseId ? await api.workOne(caseId, mode) : await api.workQueue(mode);
      setNote(caseId ? `${caseId}: ${(STATUS[r.status] || [r.status])[0].toLowerCase()} in ${Number(r.seconds || 0).toFixed(1)} s.` : `The agent worked ${plural(r.worked.length, "ticket")} in ${r.seconds} s.`);
      reload();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(null);
    }
  }

  if (loading && !data) return <div className="wrap page"><Loading what="Loading the tickets" /></div>;
  if (error) return <div className="wrap page"><Failed error={error} retry={reload} /></div>;
  const queued = data.filter((t) => t.ticket_status === "QUEUED").length;
  return (
    <main id="main" className="wrap page">
      <div className="pagehead">
        <div>
          <h1>Tickets</h1>
          <p className="lede">Every ticket CFCFRMS sent this bank, in the order it landed. The agent picks each one up at once, whatever the hour.</p>
        </div>
        {queued > 0 && (
          <button className="btn btn-primary" disabled={!!busy} onClick={() => work(null, "pipeline")}>
            {busy === "nullpipeline" ? "The agent is working them…" : `Work the ${plural(queued, "waiting ticket")}`}
          </button>
        )}
      </div>
      {note && <p className="status" role="status">{note}</p>}
      <div className="table-wrap">
        <table className="table wide">
          <thead>
            <tr><th>Landed</th><th>Ticket</th><th className="num">Reported</th><th>Notice</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr>
          </thead>
          <tbody>
            {data.map((t) => {
              const [label, pill] = STATUS[t.ticket_status] || [t.ticket_status, "pill-plain"];
              return (
                <tr key={t.case_id}>
                  <td><b>{clock(t.received_at)}</b>{t.after_hours && <div className="note">desk closed</div>}</td>
                  <td><a href={`#/case/${t.case_id}`}>{t.case_id}</a><div className="note mono">{t.utr}</div></td>
                  <td className="num">{inr(t.amount_inr)}</td>
                  <td>{t.notice_status === "VALID" ? "Complete" : <span className="error" style={{ margin: 0 }}>{t.problems}</span>}</td>
                  <td>
                    <span className={`pill ${pill}`}>{label}</span>
                    {t.run_status === "CHECK_FAILED" && <div className="note">The checker stopped the holds</div>}
                    {t.ticket_status === "DONE" && t.sim_holds_at && <div className="note">Holds in place {clock(t.sim_holds_at)}</div>}
                  </td>
                  <td className="act">
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      <button className="btn btn-sm" disabled={!!busy} onClick={() => work(t.case_id, "pipeline")}>
                        {busy === t.case_id + "pipeline" ? "Working…" : t.ticket_status === "QUEUED" ? "Work now" : "Work again"}
                      </button>
                      {modelOn && (
                        <button className="btn btn-sm btn-quiet" disabled={!!busy} onClick={() => work(t.case_id, "llm")} title="Let the local model choose the order of the tools">
                          {busy === t.case_id + "llm" ? "Working…" : "With the model"}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </main>
  );
}