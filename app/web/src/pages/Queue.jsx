import { useEffect, useState } from "react";
import { api, SNAPSHOT } from "../api.js";
import { Failed, Loading, MoreButton } from "../components/bits.jsx";
import { clock, inrR, plural, victim } from "../format.js";
import { useLoad } from "../hooks.js";

// Every complaint that reached the bank (design board "Complaints"). Status chips match Review: amber means a
// decision is waiting, red means nothing is held yet. Re-running a complaint sits in the row menu, because on a
// finished complaint it is only for a corrected police notice.
const n = (v) => Number(v || 0);
const minutesBetween = (a, b) => Math.round((new Date(String(b).replace(" ", "T")) - new Date(String(a).replace(" ", "T"))) / 60000);

function status(t) {
  if (["FAILED", "CHECK_FAILED"].includes(t.run_status)) return ["Not completed", "p-urgent"];
  if (t.ticket_status === "QUEUED") return ["Waiting", "p-plain"];
  if (t.ticket_status === "WORKING") return ["Being worked", "p-run"];
  if (n(t.waiting)) return t.waiting_code === "UNMATCHED_NOTICE" ? [`${t.waiting} for you · nothing held`, "p-urgent"] : [`${t.waiting} for you`, "p-wait"];
  if (t.ticket_status === "SENT_BACK" || t.run_status === "SENT_BACK") return ["Sent back", "p-done"];
  return ["Done", "p-done"];
}

function notice(t) {
  const back = t.sent_back_at ? ` Sent back to the police at ${clock(t.sent_back_at)}.` : "";
  switch (t.match_status) {
    case "EXACT": return "Matches the payment.";
    case "ACCEPTED": return "Near match, accepted by an officer.";
    case "NEAR_MATCH": return `Close, but it does not match the payment.${back}`;
    case "NO_MATCH": return `No such payment here.${back}`;
    case "INCOMPLETE": return `The notice is incomplete.${back}`;
    default: return "Not checked yet.";
  }
}

function heldNote(t) {
  const held = n(t.held_inr), gone = n(t.onward_inr) + n(t.exits_inr);
  if (t.linked_at && held) return `Held on arrival, ${clock(t.linked_at)}`;
  if (t.match_status === "NEAR_MATCH" && n(t.near_amount_inr)) return `${inrR(t.near_amount_inr)} still in the account`;
  if (!held && n(t.onward_inr)) return `Left Bank X before the complaint; onward entry filed`;
  if (!held && n(t.exits_inr)) return "Withdrawn as cash before the complaint";
  if (held && gone) return `${inrR(gone)} moved on earlier`;
  if (held && held >= n(t.amount_inr)) return "All of it";
  return "";
}

function firstHold(t) {
  if (t.linked_at && t.linked_at <= t.received_at) return "Before the complaint";
  if (t.first_hold_at && t.first_hold_at < t.received_at) return "Before the complaint";
  if (t.first_hold_at) return `${clock(t.first_hold_at)}, ${minutesBetween(t.received_at, t.first_hold_at)} min`;
  if (t.match_status === "NEAR_MATCH" || t.match_status === "NO_MATCH") return "Not yet";
  if (t.ticket_status === "DONE") return "Nothing to hold";
  return "–";
}

const FILTERS = [
  ["all", "All", () => true],
  ["waiting", "Decision waiting", (t) => n(t.waiting) > 0],
  ["none", "Nothing held", (t) => !n(t.held_inr) && t.ticket_status !== "QUEUED"],
  ["night", "After office hours", (t) => t.after_hours],
  ["done", "Done", (t) => !n(t.waiting) && t.ticket_status === "DONE" && !["FAILED", "CHECK_FAILED"].includes(t.run_status)],
];

function RowMenu({ t, onRun }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (!e.target.closest?.(".rowmenu")) setOpen(false); };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [open]);
  return (
    <span className="rowmenu" style={{ position: "relative", display: "inline-block" }}>
      <button className="more" type="button" aria-haspopup="menu" aria-expanded={open} aria-label={`More for the ${t.label}`} onClick={() => setOpen((o) => !o)}>⋯</button>
      {open && (
        <div className="menu" role="menu">
          <a role="menuitem" href={`#/case/${encodeURIComponent(t.case_id)}`}>Open the complaint</a>
          <a role="menuitem" href={`#/audit/${encodeURIComponent(t.case_id)}`}>See every step in the audit log</a>
          <a role="menuitem" href={`#/case/${encodeURIComponent(t.case_id)}`} title={t.run_mode === "llm" ? "A local model chose the order of the steps" : "Fixed order"}>How the steps were chosen</a>
          {!SNAPSHOT && <><hr /><button role="menuitem" type="button" onClick={() => { setOpen(false); onRun(t); }}>Run again<br /><span className="note">Only if the police send a corrected notice. Holds already placed stay.</span></button></>}
        </div>
      )}
    </span>
  );
}

export default function Queue({ onChange }) {
  const { data, error, loading, reload } = useLoad(() => api.queue(), []);
  const day = useLoad(() => api.day().catch(() => null), []);
  const [filter, setFilter] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const [note, setNote] = useState(null);

  async function run(t) {
    setNote(`Working the ${t.label} again…`);
    try {
      const r = await api.workOne(t.case_id, "pipeline");
      setNote(`The ${t.label} was worked again in ${Number(r.seconds || 0).toFixed(1)} s.`);
      reload();
      onChange?.();
    } catch (e) {
      setNote(e.message);
    }
  }

  if (loading && !data) return <div className="wrap"><Loading what="Loading the complaints" /></div>;
  if (error) return <div className="wrap"><Failed error={error} retry={reload} /></div>;
  const reported = data.reduce((s, t) => s + n(t.amount_inr), 0);
  const worked = data.filter((t) => t.first_hold_at && t.first_hold_at >= t.received_at);
  const max = worked.length ? Math.max(...worked.map((t) => minutesBetween(t.received_at, t.first_hold_at))) : null;
  const rows = data.filter(FILTERS.find((f) => f[0] === filter)[2]);
  return (
    <main id="main" className="wrap" style={{ paddingTop: 24, paddingBottom: 40 }}>
      <h1 style={{ fontSize: 30 }}>{plural(data.length, "complaint")} today, {inrR(reported)} reported</h1>
      <p style={{ fontSize: 15, color: "#44505e", marginTop: 4, marginBottom: 18 }}>
        {data.length ? `${data.length === 1 ? "It was" : `All ${data.length} were`} worked by the system${max != null ? ` within ${plural(max, "minute")}` : ""}.` : "No complaints have reached the bank yet."}
        {day.data?.tool ? ` ${inrR(day.data.tool.victim_held_inr)} of victims' money is held at Bank X.` : ""}
      </p>
      {note && <p className="card" role="status" style={{ padding: "8px 12px", marginBottom: 12 }}>{note}</p>}
      <div className="chips" style={{ marginBottom: 12 }}>
        {FILTERS.map(([k, label, fn]) => <button key={k} type="button" className={`chip${filter === k ? " on" : ""}`} onClick={() => setFilter(k)}>{label} <span>{data.filter(fn).length}</span></button>)}
      </div>
      <div className="card" style={{ overflow: "visible" }}>
        <table className="t">
          <thead><tr><th scope="col">Received</th><th scope="col">Victim</th><th scope="col" className="r">Reported</th><th scope="col">Police notice</th>
            <th scope="col" className="r">Held at Bank X</th><th scope="col">First hold</th><th scope="col">Status</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
          <tbody>
            {(showAll ? rows : rows.slice(0, 25)).map((t) => {
              const [word, cls] = status(t);
              return (
                <tr key={t.case_id}>
                  <td><a className="mono" href={`#/case/${encodeURIComponent(t.case_id)}`} style={{ fontWeight: 600, fontSize: 15 }}>{clock(t.received_at)}</a>{t.after_hours && <><br /><span className="pill p-night">After hours</span></>}</td>
                  <td><b>{victim(t.victim_tag)}</b>{t.from_bank && <><br /><span className="note">onward from {t.from_bank}</span></>}</td>
                  <td className="r">{inrR(t.amount_inr)}</td>
                  <td style={{ maxWidth: 320 }}>{notice(t)}</td>
                  <td className="r"><b>{inrR(t.held_inr || 0)}</b>{heldNote(t) && <><br /><span className="note">{heldNote(t)}</span></>}</td>
                  <td>{firstHold(t)}</td>
                  <td><span className={`pill ${cls}`}>{word}</span></td>
                  <td className="r"><RowMenu t={t} onRun={run} /></td>
                </tr>
              );
            })}
            {!rows.length && <tr><td colSpan={8} className="note">No complaints in this view.</td></tr>}
          </tbody>
        </table>
      </div>
      {rows.length > 25 && <MoreButton open={showAll} setOpen={setShowAll} label={`Show all ${rows.length} complaints`} style={{ marginTop: 10 }} />}
      <p className="note" style={{ marginTop: 10 }}>Status chips are the same here and on Review: amber means a decision is waiting, red means nothing is held yet.</p>
    </main>
  );
}