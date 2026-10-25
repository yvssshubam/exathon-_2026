import { useState } from "react";
import { api } from "../api.js";
import { Failed, Loading } from "../components/bits.jsx";
import { clock, inr, plural, victim } from "../format.js";
import { useLoad } from "../hooks.js";

const STATUS = {
  QUEUED: ["Waiting", "pill-plain"],
  WORKING: ["Being handled", "pill-plain"],
  DONE: ["Done by the system", "pill-held"],
  SENT_BACK: ["Sent back to the police", "pill-other"],
  LINKED: ["Linked to an earlier case", "pill-plain"],
  FAILED: ["Could not be completed", "pill-gone"],
};
const MATCH = {
  EXACT: "Matches the payment", ACCEPTED: "Near match accepted by the officer", NEAR_MATCH: "Near match only",
  NO_MATCH: "No such payment here", INCOMPLETE: "Incomplete",
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
      const r = await api.workOne(caseId, mode);
      setNote(`${caseId}: ${(STATUS[r.status] || [r.status])[0].toLowerCase()} in ${Number(r.seconds || 0).toFixed(1)} s.`);
      reload();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(null);
    }
  }

  if (loading && !data) return <div className="wrap page"><Loading what="Loading the tickets" /></div>;
  if (error) return <div className="wrap page"><Failed error={error} retry={reload} /></div>;
  return (
    <main id="main" className="wrap page">
      <div className="pagehead">
        <div>
          <h1>Complaints</h1>
          <p className="lede">Every complaint that reached this bank, in the order it arrived: from the police, and onward entries other banks filed. The system picks each one up the minute it lands, whatever the hour. {plural(data.length, "complaint")}.</p>
        </div>
      </div>
      {note && <p className="status" role="status">{note}</p>}
      <div className="table-wrap">
        <table className="table wide">
          <thead>
            <tr><th>Complaint</th><th className="num">Reported</th><th>Intake</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr>
          </thead>
          <tbody>
            {data.map((t) => {
              const [label, pill] = STATUS[t.ticket_status] || [t.ticket_status, "pill-plain"];
              return (
                <tr key={t.case_id}>
                  <td><a href={`#/case/${t.case_id}`}><b>{t.label || t.case_id}</b></a>
                    <div className="note">{[victim(t.victim_tag) && `${victim(t.victim_tag)}'s money`, t.after_hours && "after office hours"].filter(Boolean).join(" · ")}</div>
                    <div className="note mono">UTR {t.utr}</div></td>
                  <td className="num">{inr(t.amount_inr)}</td>
                  <td>{MATCH[t.match_status] || "Not checked yet"}{t.related && t.related !== "NEW" && <div className="note">{t.related.toLowerCase().replace("_", " ")} </div>}
                    {(t.mismatched || t.problems) && <div className="note" style={{ whiteSpace: "pre-wrap" }}>{t.mismatched || t.problems}</div>}</td>
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
                        <button className="btn btn-sm btn-quiet" disabled={!!busy} onClick={() => work(t.case_id, "llm")} title="Let the local model choose the order of the steps">
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