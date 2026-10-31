import { useContext, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, SNAPSHOT } from "../api.js";
import { Failed, Loading } from "../components/bits.jsx";
import SidePanel, { PanelFooter } from "../components/SidePanel.jsx";
import { clock, dayTime, inrR, plural, shortName } from "../format.js";
import { useLoad } from "../hooks.js";

// Police notices that arrive by e-mail or PDF (D-52), read and checked field by field against the payment, with the
// letter sent back when one is wrong; and pre-alerts for likely unreported victims (D-53), with where the account is
// on hold. Each opens in the side panel.

const num = (v) => Number(v || 0);
const NOTICE_STATUS = {
  MATCHED: ["Matches its ticket", "p-clear"],
  SENT_BACK: ["Needs correction", "p-look"],
  READ: ["Being checked", "p-plain"],
  UNREADABLE: ["Could not be read", "p-urgent"],
};
const RESULT = {
  match: ["Matches", "r-for"],
  given: ["Given", "r-for"],
  differs: ["Differs", "r-against"],
  missing: ["Missing", "r-against"],
  not_found: ["Not found", "r-against"],
  no_record: ["Nothing to compare", "r-none"],
};

function noticeStatus(n) {
  if (n.notice_status === "SENT_BACK" && n.sent_at) return [`Letter sent ${dayTime(n.sent_at)}`, "p-done"];
  return NOTICE_STATUS[n.notice_status] || [n.notice_status, "p-plain"];
}

const ALERT_WORDS = { ANSWERED: ["The payer complained", "p-clear"], CONFIRMED: ["The payer says fraud", "p-held"],
  GENUINE: ["The payer says genuine", "p-wait"], NOT_NEEDED: ["Closed · not needed", "p-done"] };
function alertStatus(a) {
  if (a.alert_status === "SENT") return [`Sent ${dayTime(a.sent_at)}`, "p-done"];
  if (ALERT_WORDS[a.alert_status]) return ALERT_WORDS[a.alert_status];
  if (a.reported_case_id) return [`Victim reported ${clock(a.reported_at)}`, "p-clear"];
  if (a.alert_status === "NOT_SENT") return ["Not sent", "p-plain"];
  if (a.closed_note) return ["Closed · not needed", "p-done"];
  return ["Ready to send", "p-urgent"];
}

// the buttons go to the side panel's pinned footer, so they stay on screen while the officer scrolls
function Pinned({ children }) {
  const footer = useContext(PanelFooter);
  return footer ? createPortal(children, footer) : <div style={{ marginTop: 14 }}>{children}</div>;
}

export default function Notices({ officer, onChange }) {
  const notices = useLoad(() => api.notices(), []);
  const alerts = useLoad(() => api.prealerts().catch(() => []), []);
  const [open, setOpen] = useState(null);
  const list = useRef(null);
  const reload = () => { notices.reload(); alerts.reload(); onChange?.(); };
  if (notices.loading && !notices.data) return <div className="wrap"><Loading what="Loading the notices" /></div>;
  if (notices.error) return <div className="wrap"><Failed error={notices.error} retry={notices.reload} /></div>;
  const ns = notices.data.notices || [];
  const as = alerts.data || [];
  const nt = open?.kind === "notice" ? ns.find((n) => n.notice_id === open.id) : null;
  const al = open?.kind === "alert" ? as.find((a) => a.alert_id === open.id) : null;
  const toSend = ns.filter((n) => n.notice_status === "SENT_BACK" && !n.sent_at).length + as.filter((a) => a.waiting).length;
  return (
    <main id="main" className="wrap" style={{ paddingTop: 24, paddingBottom: 40 }}>
      <h1 style={{ fontSize: 24, marginBottom: 6 }}>Notices and pre-alerts {toSend > 0 && <span className="pill p-look" style={{ verticalAlign: "middle" }}>{toSend} to send</span>}</h1>
      <p className="note" style={{ fontSize: 14, maxWidth: 820, marginBottom: 20 }}>Police notices that arrive by e-mail or PDF are read here and checked field by field against the payment and its ticket. A notice with a mistake gets a letter asking the police to correct it. Nothing is held on a notice alone: holds come from tickets.</p>
      <div ref={list} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 24, maxWidth: 820 }}>
        <ReadNotice samples={notices.data.samples || []} onRead={(r) => { reload(); setOpen({ kind: "notice", id: r.notice_id }); }} />
        {as.length > 0 && <AlertList list={as} open={open} setOpen={setOpen} />}
        <NoticeList list={ns} open={open} setOpen={setOpen} />
      </div>
      {nt && <SidePanel title={`Notice from ${nt.sent_from || nt.authority || "the police"}`} itemKey={nt.notice_id} onClose={() => setOpen(null)} beside={list}>
        <NoticePanel key={nt.notice_id} nt={nt} />
      </SidePanel>}
      {al && <SidePanel title={al.proactive ? `Why ${shortName(al.account_name)} is on the watch${al.type_name ? ` · ${al.type_name}` : ""}` : "Pre-alert"} itemKey={`a${al.alert_id}`} onClose={() => setOpen(null)} beside={list}>
        <AlertPanel key={al.alert_id} a={al} officer={officer} onSent={reload} />
      </SidePanel>}
    </main>
  );
}

function ReadNotice({ samples, onRead }) {
  const [how, setHow] = useState("paste");
  const [text, setText] = useState("");
  const [from, setFrom] = useState("");
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  async function read(body, key) {
    setBusy(key);
    setError(null);
    try {
      const r = await api.readNotice(body);
      setText("");
      setFile(null);
      onRead(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  }
  async function submit(e) {
    e.preventDefault();
    if (how === "paste") return read({ text, sent_from: from || null }, "read");
    if (!file) return setError("Choose a PDF first.");
    const data = await new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = fail; r.readAsDataURL(file); });
    return read({ pdf_base64: String(data), file_name: file.name, sent_from: from || null }, "read");
  }
  const saved = !!SNAPSHOT;
  return (
    <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="read-h">
      <h2 id="read-h" style={{ fontSize: 18, marginBottom: 12 }}>Read a notice</h2>
      <div className="chips" role="tablist" aria-label="How the notice came" style={{ marginBottom: 12 }}>
        {[["paste", "Paste an e-mail"], ["pdf", "Upload a PDF"]].map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={how === k} className={`chip${how === k ? " on" : ""}`} onClick={() => { setHow(k); setError(null); }}>{label}</button>
        ))}
      </div>
      <form className="form" onSubmit={submit}>
        {how === "paste" ? (
          <label>The notice, as received
            <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the e-mail here" required disabled={saved} />
          </label>
        ) : (
          <label>The PDF (typed, or a scan)
            <input type="file" accept="application/pdf,.pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} disabled={saved} />
            <span className="note" style={{ fontWeight: 400 }}>A typed PDF is read from its text. A scan with no text in it is read by OCR when Tesseract is installed.</span>
          </label>
        )}
        <label>From (optional)
          <input value={from} onChange={(e) => setFrom(e.target.value)} placeholder="For example cybercell.vlr@police.example" disabled={saved} />
        </label>
        {error && <p role="alert" style={{ color: "#7a211b", fontSize: 14 }}>{error}</p>}
        <div className="acts">
          <button className="btn btn-p" type="submit" disabled={saved || !!busy}>{busy === "read" ? "Reading and checking…" : "Read and check"}</button>
          {saved && <span className="note">Saved copy: nothing can be read here.</span>}
        </div>
      </form>
      {samples.length > 0 && (
        <div style={{ marginTop: 16, borderTop: "1px solid #e8ecf0", paddingTop: 14 }}>
          <p className="note" style={{ marginBottom: 8 }}>Or try a sample notice:</p>
          <div className="chips">
            {samples.map((s) => (
              <button key={s.name} type="button" className="chip" title={s.about} disabled={saved || !!busy} onClick={() => read({ sample: s.name }, s.name)}>{busy === s.name ? "Reading…" : s.name}</button>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function NoticeList({ list, open, setOpen }) {
  return (
    <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="nl-h">
      <h2 id="nl-h" style={{ fontSize: 18, marginBottom: 12 }}>Notices read · {list.length}</h2>
      {list.length === 0 ? <p className="note">No notices yet. Paste one, upload a PDF, or try a sample.</p> : (
        <div className="tw">
          <table className="t">
            <thead><tr><th scope="col">Received</th><th scope="col">From</th><th scope="col">The notice says</th><th scope="col">Result</th><th /></tr></thead>
            <tbody>
              {list.map((n) => {
                const isOpen = open?.kind === "notice" && open.id === n.notice_id;
                const [label, cls] = noticeStatus(n);
                const pick = (e) => { if (!e.target.closest("button, a")) setOpen({ kind: "notice", id: n.notice_id }); };
                return (
                  <tr key={n.notice_id} className={`pick${isOpen ? " open" : ""}`} onClick={pick}>
                    <td><span className="mono" style={{ whiteSpace: "nowrap" }}>{dayTime(n.received_at)}</span><br /><span className="note">{n.channel}</span></td>
                    <td>{n.sent_from || n.authority || "–"}</td>
                    <td><span className="mono" style={{ fontSize: 13 }}>{n.utr || "no UTR"}</span><br /><span className="note">{n.amount_inr != null ? inrR(n.amount_inr) : "no amount"}</span></td>
                    <td>
                      <span className={`pill ${cls}`}>{label}</span>
                      {n.notice_status === "SENT_BACK" && <><br /><span className="note">{plural(n.problems.length, "problem")}</span></>}
                      {n.label && <><br /><span className="note">{n.label}</span></>}
                    </td>
                    <td className="r"><button className="btn btn-s" type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : { kind: "notice", id: n.notice_id })}>{isOpen ? "Close" : "Open"}</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function AlertList({ list, open, setOpen }) {
  return (
    <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="pa-h">
      <h2 id="pa-h" style={{ fontSize: 18, marginBottom: 4 }}>Pre-alerts · {list.length}</h2>
      <p className="note" style={{ fontSize: 14, marginBottom: 12 }}>A first-time payer sent money into an account a victim had already named, and it was held at once. Someone who can reach the payer is told, so the payer can say whether they meant to pay: our fraud team for our own customers, the police through CFCFRMS for other banks' customers.</p>
      <div className="tw">
        <table className="t">
          <thead><tr><th scope="col">Arrived</th><th scope="col">Into</th><th scope="col" className="r">Amount</th><th scope="col">Status</th><th /></tr></thead>
          <tbody>
            {list.map((a) => {
              const isOpen = open?.kind === "alert" && open.id === a.alert_id;
              const [label, cls] = alertStatus(a);
              const pick = (e) => { if (!e.target.closest("button, a")) setOpen({ kind: "alert", id: a.alert_id }); };
              return (
                <tr key={a.alert_id} className={`pick${isOpen ? " open" : ""}`} onClick={pick}>
                  <td><span className="mono" style={{ whiteSpace: "nowrap" }}>{dayTime(a.received_at)}</span></td>
                  <td><b>{shortName(a.account_name)}</b><br /><span className="note">from {a.sender_text}</span></td>
                  <td className="r">{inrR(a.amount_inr)}</td>
                  <td><span className={`pill ${cls}`}>{label}</span><br /><span className="note">{inrR(a.held_total_inr)} on hold</span></td>
                  <td className="r"><button className="btn btn-s" type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : { kind: "alert", id: a.alert_id })}>{isOpen ? "Close" : "Open"}</button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function NoticePanel({ nt }) {
  const sent = !!nt.sent_at;
  const [label, cls] = noticeStatus(nt);
  const lead = nt.notice_status === "MATCHED"
    ? `Every field matches the payment and the ${nt.label || "ticket"}. Nothing more to do: the hold follows the ticket.`
    : nt.notice_status === "UNREADABLE" ? "The notice has no text the bank can read. It waits for you."
    : nt.notice_status === "READ" ? "Read into a ticket; the agent is checking it."
    : `${plural(nt.problems.length, "problem")}. ${sent ? "The letter below went to the police." : "A letter asking the police to correct it is drafted below."}`;
  return (
    <article className="card dc narrowcard">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
        <span className="kind">{nt.channel} · {dayTime(nt.received_at)}</span>
        <span className={`pill ${cls}`}>{label}</span>
      </div>
      <p style={{ fontSize: 15, marginBottom: 6 }}><b>{lead}</b></p>
      <p className="note" style={{ marginBottom: 14 }}>Read from {nt.read_from || "the text"}{nt.read_by === "model" ? ", with the local model (every value it gave is in the notice)" : ""}.{nt.file_name ? ` File: ${nt.file_name}.` : ""}</p>
      {nt.checks.length > 0 && <>
        <h3 style={{ fontSize: 15, marginBottom: 8 }}>Field by field</h3>
        <div className="chk" style={{ marginBottom: 12 }}>
          <div className="chk-list" role="table" aria-label="Each field of the notice against the bank's records">
            {nt.checks.map((c) => {
              const optional = c.result === "missing" && c.note;
              const [word, tone] = optional ? ["Not given", "r-none"] : RESULT[c.result] || [c.result, "r-none"];
              return (
                <div key={c.field} className="chk-row" role="row">
                  <span role="cell" className="chk-what">{c.label}</span>
                  <span role="cell" className="chk-val">
                    <span style={{ display: "block" }}><span className="note">Notice: </span>{c.notice ?? <i className="note">not given</i>}</span>
                    {c.records && <span style={{ display: "block" }}><span className="note">Records: </span>{c.records}</span>}
                  </span>
                  <span role="cell" className="chk-reads"><span className={`reads ${tone}`}>{word}</span></span>
                  {c.note && <span className="chk-why">{c.note}</span>}
                </div>
              );
            })}
          </div>
        </div>
      </>}
      {nt.agent_notes.length > 0 && <div className="against" style={{ marginBottom: 12 }}><b>For you:</b> {nt.agent_notes.join(" ")}</div>}
      {nt.notice_status === "SENT_BACK" && nt.reply_text && <>
        <h3 style={{ fontSize: 15, margin: "16px 0 8px" }}>{sent ? `Letter sent by ${nt.sent_by}, ${dayTime(nt.sent_at)}` : "The letter to the police"}</h3>
        <label className="sr" htmlFor={`letter-${nt.notice_id}`}>The letter</label>
        <textarea id={`letter-${nt.notice_id}`} rows={14} value={nt.reply_text} readOnly style={{ width: "100%", fontSize: 14, lineHeight: 1.5 }} />
        <p className="note" style={{ marginTop: 6 }}>Written from the fixed template, naming each field that does not match. Recorded in the audit log.</p>
      </>}
      <details style={{ marginTop: 14 }}>
        <summary style={{ cursor: "pointer", fontSize: 14, fontWeight: 600 }}>The notice as received</summary>
        <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, background: "#f7f9fa", padding: 10, borderRadius: 8, marginTop: 8 }}>{nt.raw_text}</pre>
      </details>
      {nt.case_id && <p style={{ marginTop: 12 }}><a href={`#/case/${encodeURIComponent(nt.case_id)}`}>Open the ticket it became</a></p>}
    </article>
  );
}

function AlertPanel({ a, officer, onSent }) {
  const [text, setText] = useState(a.alert_text || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const sent = a.alert_status === "SENT";
  const [label, cls] = alertStatus(a);
  async function send() {
    setBusy(true);
    setError(null);
    try {
      if (a.proactive) await api.sendPreAlert(a.pre_alert_id, { officer, reason: "The payer should be asked whether they meant to pay." });
      else await api.approve(a.alert_id, { officer, signed_text: text });
      onSent();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="card dc narrowcard">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
        <span className="kind">Pre-alert · {dayTime(a.received_at)}</span>
        <span className={`pill ${cls}`}>{label}</span>
      </div>
      <h4 style={{ fontSize: 19, marginBottom: 8 }}>{inrR(a.amount_inr)} into {shortName(a.account_name)} from a first-time payer</h4>
      {a.proactive ? (
        <>
          <p style={{ fontSize: 15, marginBottom: 12 }}>At {clock(a.received_at)} {shortName(a.account_name)} received {inrR(a.amount_inr)} from {a.sender_text} (UTR <span className="mono" style={{ fontSize: 13 }}>{a.utr || "–"}</span>). The payer had not paid this account before, and no complaint named the payment. {inrR(a.held_inr)} of it was held the same minute{a.legal_basis === "rbi_draft" ? ", a temporary debit hold under the RBI draft" : ", under the existing police notice"}.</p>
          <div className="facts" style={{ marginBottom: 12 }}>
            <span>Why it is on the watch</span><b style={{ textAlign: "right" }}>{a.eligibility_reason === "suspect_registry" ? "Listed in the Suspect Registry" : "A victim paid it directly (Layer 1)"}</b>
            <span>Account type</span><b style={{ textAlign: "right" }}>{a.type_name || "–"}{a.pass_through != null ? ` (pass-through ${num(a.pass_through).toFixed(2)}, fraud exposure ${num(a.fraud_exposure).toFixed(2)})` : ""}</b>
            <span>Route</span><b style={{ textAlign: "right" }}>{a.route === "fraud_team" ? "A call by our fraud team" : "The police, through CFCFRMS"}</b>
            {a.minutes_early != null && <><span>Held before the complaint</span><b style={{ textAlign: "right" }}>{a.minutes_early} minutes</b></>}
          </div>
          {a.ending_note && <div className="changed" style={{ marginBottom: 12 }}>{a.ending_note}</div>}
          {a.complaint_draft && <p style={{ fontSize: 14, marginBottom: 12 }}><b>The NCRP complaint the bank files for its customer:</b> {a.complaint_draft}</p>}
        </>
      ) : (
      <p style={{ fontSize: 15, marginBottom: 12 }}>At {clock(a.received_at)} {shortName(a.account_name)}, already on hold, received {inrR(a.amount_inr)} from {a.sender_text} (UTR <span className="mono" style={{ fontSize: 13 }}>{a.utr || "–"}</span>). That sender never paid this account before, and no complaint named the payment. {num(a.held_inr) > 0 ? `${inrR(a.held_inr)} of it is held under the existing notice, not linked to a reported victim yet.` : "None of it could be held: the existing notice already covers its full amount."}</p>
      )}
      {a.reported_case_id && <div className="changed" style={{ marginBottom: 12 }}><b>The victim has since reported.</b> The {a.reported_label || "new complaint"} names this payment, so the hold is linked to them. This pre-alert no longer needs to go.</div>}
      {!a.reported_case_id && a.closed_note && <div className="changed" style={{ marginBottom: 12 }}><b>{a.alert_status === "NOT_SENT" ? "Not sent." : "Closed."}</b> {a.closed_note}</div>}
      {!a.proactive && <h3 style={{ fontSize: 15, marginBottom: 8 }}>Where this account is on hold now</h3>}
      {!a.proactive && <div className="chk" style={{ marginBottom: 12 }}>
        <div className="chk-list" role="table" aria-label="Active holds on this account">
          {a.holds.map((h) => (
            <div key={h.hold_id} className="chk-row" role="row" style={h.this_payment ? { background: "#faf8fe" } : undefined}>
              <span role="cell" className="chk-what">{h.what[0].toUpperCase() + h.what.slice(1)}</span>
              <span role="cell" className="chk-val">Since {dayTime(h.since)}{h.authority ? ` · ordered by ${h.authority}` : ""}</span>
              <span role="cell" className="chk-reads"><b>{inrR(h.amount_inr)}</b></span>
            </div>
          ))}
          <div className="chk-total" role="row"><span role="cell">Total on hold</span><span role="cell"><b>{inrR(a.held_total_inr)}</b></span></div>
        </div>
        {a.holds.length === 0 && <p className="note" style={{ marginTop: 6 }}>No active holds on this account now.</p>}
      </div>}
      <h3 style={{ fontSize: 15, margin: "16px 0 8px" }}>{a.sent_at ? `Sent by ${a.sent_by === "agent" ? "the system, at once (Pipe for fraud)" : a.sent_by}, ${dayTime(a.sent_at)}` : "The pre-alert"}</h3>
      <label className="sr" htmlFor={`alert-${a.alert_id}`}>The pre-alert</label>
      <textarea id={`alert-${a.alert_id}`} rows={a.proactive ? 6 : 14} value={text} onChange={(e) => setText(e.target.value)} readOnly={!a.waiting || a.proactive} style={{ width: "100%", fontSize: 14, lineHeight: 1.5 }} />
      {a.proactive && <p className="note" style={{ marginTop: 4 }}>A fixed template filled from the bank's records: facts only, never written by the model.</p>}
      {a.waiting && (
        <Pinned>
          <div className="acts">
            <button className="btn btn-p" type="button" disabled={busy || !officer || !!SNAPSHOT || text.trim().length < 20} onClick={send}>{busy ? "Recording…" : a.proactive ? "Send the pre-alert" : "Mark pre-alert as sent"}</button>
            <span className="note">{officer ? "Records it in the audit log. The build sends no e-mail." : "Sign in to send it."}</span>
          </div>
          {error && <p role="alert" style={{ color: "#7a211b", fontSize: 14, marginTop: 6 }}>{error}</p>}
        </Pinned>
      )}
    </article>
  );
}