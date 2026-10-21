import { useState } from "react";
import { api } from "../api.js";
import { clock, inr } from "../format.js";
import { useLoad } from "../hooks.js";
import { Failed, Loading, NightBadge } from "../components/bits.jsx";

const STATUS = { QUEUED: "Queued", WORKING: "Working", DONE: "Done", SENT_BACK: "Sent back", FAILED: "Failed" };

// The simulated CFCFRMS queue at the bank, in the order tickets landed.
export default function Queue({ modelOn }) {
  const { data, error, loading, reload } = useLoad(() => api.queue(), []);
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);

  async function work(caseId, mode) {
    setBusy(caseId + mode);
    setNote(null);
    try {
      const r = await api.workOne(caseId, mode);
      setNote(`${caseId}: ${STATUS[r.status === "CHECK_FAILED" ? "DONE" : r.status] || r.status} in ${r.seconds.toFixed(1)} s.`);
      reload();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(null);
    }
  }

  if (loading && !data) return <Loading what="Loading the ticket queue" />;
  if (error) return <Failed error={error} retry={reload} />;
  return (
    <>
      <header className="page-head">
        <div>
          <h1>Ticket queue</h1>
          <p className="lede">Every ticket CFCFRMS sent this bank, in the order it landed. The agent picks each one up at once, whatever the hour.</p>
        </div>
      </header>
      {note && <p className="note" role="status">{note}</p>}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Landed</th><th>Ticket</th><th className="num">Reported</th><th>Notice</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr>
          </thead>
          <tbody>
            {data.map((t) => (
              <tr key={t.case_id}>
                <td><b>{clock(t.received_at)}</b> <NightBadge on={t.after_hours} /></td>
                <td>{t.ticket_status === "QUEUED" ? t.case_id : <a href={`#/case/${t.case_id}`}>{t.case_id}</a>}<br /><span className="ident">{t.utr}</span></td>
                <td className="num">{inr(t.amount_inr)}</td>
                <td>{t.notice_status === "VALID" ? "Complete" : <span className="warn">{t.problems}</span>}</td>
                <td>{STATUS[t.ticket_status] || t.ticket_status}{t.run_status === "CHECK_FAILED" ? " (checker stopped it)" : ""}</td>
                <td className="row">
                  <button className="btn" disabled={!!busy} onClick={() => work(t.case_id, "pipeline")}>
                    {busy === t.case_id + "pipeline" ? "Working…" : t.ticket_status === "QUEUED" ? "Work now" : "Work again"}
                  </button>
                  {modelOn && (
                    <button className="btn btn-ghost" disabled={!!busy} onClick={() => work(t.case_id, "llm")} title="Let the local model choose the order of the tools">
                      {busy === t.case_id + "llm" ? "Working…" : "With the model"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}