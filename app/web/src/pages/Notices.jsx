import { useState } from "react";
import { api } from "../api.js";
import { Failed, Loading } from "../components/bits.jsx";
import { clock, inr, plural } from "../format.js";
import { useLoad } from "../hooks.js";

// Police notices that arrive by e-mail or post (pipeline/notices.py): what the agent read, what it checked in
// Exasol, and what it decided. Nothing is held on a notice alone; holds come from tickets.
const STATUS = {
  NEW: ["Not read yet", "pill-plain"],
  LINKED: ["Matches its ticket", "pill-honest"],
  NO_TICKET: ["Valid, no ticket yet", "pill-other"],
  SENT_BACK: ["Sent back for correction", "pill-alert"],
};
const FIELD_LABELS = [["utr", "UTR"], ["amount_inr", "Amount"], ["txn_date", "Payment date"],
  ["legal_basis", "Legal provision"], ["authority", "Police unit"], ["notice_ref", "Notice reference"], ["ack_no", "Acknowledgement no."]];

function value(n, k) {
  const v = n[k];
  if (v === null || v === undefined || v === "") return <span className="muted">not in the notice</span>;
  if (k === "amount_inr") return inr(v);
  if (k === "txn_date") return String(v).slice(0, 10);
  return <span className={k === "utr" || k === "ack_no" ? "mono" : ""}>{v}</span>;
}

function NoticeCard({ n, officer, onDone }) {
  const [open, setOpen] = useState(n.notice_status === "SENT_BACK" && !n.sent_at);
  const [text, setText] = useState(n.reply_text || "");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [label, pill] = STATUS[n.notice_status] || [n.notice_status, "pill-plain"];

  async function send() {
    if (!officer.trim()) return setError("Type your name in the Officer box at the top first.");
    setBusy(true);
    setError(null);
    try { await api.sendLetter(n.notice_id, { officer, text }); onDone(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  return (
    <article className="card decision">
      <div className="decision-head">
        <div>
          <h4>{n.notice_id} · {n.channel}, {clock(n.received_at)}</h4>
          <div className="muted">From {n.sent_from}{n.read_at ? ` · read by the agent at ${clock(n.read_at)}` : ""}</div>
        </div>
        <span className={`pill ${pill} pill-lg`}>{label}</span>
      </div>

      {n.notice_status === "LINKED" && (
        <p style={{ marginBottom: 12 }}>
          Matches the <a href={`#/case/${n.case_id}`}>{clock(n.ticket_received_at)} ticket ({n.case_id})</a>: the UTR, amount and date agree with the transaction, and it cites {n.legal_basis}.
        </p>
      )}
      {n.notice_status === "NO_TICKET" && <p style={{ marginBottom: 12 }}>The notice checks out, but no ticket for this payment has arrived yet. Open one on the portal to start the holds.</p>}
      {n.problems.length > 0 && (
        <>
          <h5 className="why-h">Why it cannot be acted on</h5>
          <ol className="problems">{n.problems.map((p) => <li key={p}>{p.replace(/Rs (?=\d)/g, "₹")}</li>)}</ol>
        </>
      )}
      {n.agent_notes.map((t) => <p key={t} className="status" style={{ margin: "0 0 12px" }}>{t}</p>)}

      <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary>{n.notice_status === "SENT_BACK" ? (n.sent_at ? `Letter sent by ${n.sent_by}` : "The letter to the police, drafted by the agent") : "What the agent read"}</summary>
        <div className="noticegrid">
          <div>
            <h5 className="why-h">The notice as received</h5>
            <pre className="raw">{n.raw_text}</pre>
          </div>
          <div>
            <h5 className="why-h">What the agent read{n.read_by ? ` (${n.read_by === "model" ? "local model, every value found in the notice" : "fixed parser"})` : ""}</h5>
            <table className="evtable"><tbody>
              {FIELD_LABELS.map(([k, l]) => <tr key={k}><td>{l}</td><td>{value(n, k)}</td></tr>)}
            </tbody></table>
          </div>
        </div>
        {n.notice_status === "SENT_BACK" && (
          n.sent_at ? <pre className="raw">{n.reply_text}</pre> : (
            <div className="decide">
              <label>The letter{n.reply_by === "model" ? " (worded by the local model; every number and UTR checked)" : ""}
                <textarea rows="12" value={text} onChange={(e) => setText(e.target.value)} />
              </label>
              {error && <p className="error" role="alert">{error}</p>}
              <div className="row">
                <button className="btn btn-primary" disabled={busy} onClick={send}>{busy ? "Sending…" : "Send to the police"}</button>
                {text !== n.reply_text && <button type="button" className="linkbtn" onClick={() => setText(n.reply_text)}>Restore the draft</button>}
              </div>
              <p className="note">Recorded in the audit log with your name. This build does not send e-mail.</p>
            </div>
          )
        )}
      </details>
    </article>
  );
}

export function noticeSummary(list) {
  const n = (s) => (list || []).filter((x) => x.notice_status === s).length;
  return [n("LINKED") && `${plural(n("LINKED"), "notice")} matched ${n("LINKED") === 1 ? "its ticket" : "their tickets"}`,
    n("NO_TICKET") && `${n("NO_TICKET")} valid with no ticket yet`,
    n("SENT_BACK") && `${n("SENT_BACK")} sent back for correction`].filter(Boolean).join(", ");
}

export default function Notices({ officer }) {
  const { data, error, loading, reload } = useLoad(() => api.notices(), []);
  if (loading && !data) return <div className="wrap page"><Loading what="Loading the notices" /></div>;
  if (error) return <div className="wrap page"><Failed error={error} retry={reload} /></div>;
  const read = data.filter((n) => n.notice_status !== "NEW");
  return (
    <main id="main" className="wrap page">
      <div className="pagehead">
        <div>
          <h1>Police notices</h1>
          <p className="lede">Notices that reach the desk by e-mail or post. The agent reads each one, checks it against the transactions, and either matches it to its ticket or drafts a letter asking for a correction. Nothing is held on a notice alone.</p>
        </div>
      </div>
      {read.length ? <p className="status" role="status">{plural(read.length, "notice")} read: {noticeSummary(data)}.</p>
        : <p className="empty">The agent has not read any notices yet. Replay the day on the Review page.</p>}
      {data.map((n) => <NoticeCard key={n.notice_id} n={n} officer={officer} onDone={reload} />)}
    </main>
  );
}