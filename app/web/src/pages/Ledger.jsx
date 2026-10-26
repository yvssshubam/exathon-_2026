import { useState } from "react";
import { api } from "../api.js";
import { KEY_EVENTS, shortEvent } from "../components/DecisionCard.jsx";
import { Failed, Loading } from "../components/bits.jsx";
import { accountName, clock, inrR, joinAnd, plural, rs, shortName, victim } from "../format.js";
import { useLoad } from "../hooks.js";

const n = (v) => Number(v || 0);

// ---------------------------------------------------------------- Holds (design board "Holds")
// One ledger table, one row per account, plus a three-part total: victims' money, genuine customers' own money,
// and any officer-ordered freeze, which is nobody's traced money. Each row says what happens next.
export function Holds() {
  const holds = useLoad(() => api.holds(), []);
  const inbox = useLoad(() => api.inbox().catch(() => null), []);
  const [filter, setFilter] = useState("all");
  if (holds.loading && !holds.data) return <div className="wrap"><Loading what="Loading the hold ledger" /></div>;
  if (holds.error) return <div className="wrap"><Failed error={holds.error} retry={holds.reload} /></div>;
  const waiting = inbox.data?.waiting || [];
  const watch = inbox.data?.watch || [];
  const active = holds.data.filter((h) => h.hold_status === "ACTIVE");
  const accounts = [];
  for (const h of active) {
    let a = accounts.find((x) => x.id === h.account_id);
    if (!a) accounts.push(a = { id: h.account_id, name: h.holder_name, liens: [], freeze: null, marker: null });
    if (h.hold_kind === "LIEN") a.liens.push(h);
    else if (h.hold_kind === "ACCOUNT_FREEZE") a.freeze = h;
    else a.marker = h;
  }
  for (const a of accounts) {
    a.victims = a.liens.filter((h) => h.hold_tag !== "NOT_LINKED");
    a.unlinked = a.liens.filter((h) => h.hold_tag === "NOT_LINKED");
    a.lien = a.liens.reduce((t, h) => t + n(h.amount_inr), 0);
    a.decision = waiting.find((p) => p.account_id === a.id);
    a.own = a.decision?.action_code === "PROPOSE_NARROW" ? Math.max(0, n(a.decision.current_hold_inr) - n(a.decision.amount_inr)) : 0;
    a.held = Math.max(a.lien, n(a.freeze?.amount_inr));
    a.victimMoney = a.victims.reduce((t, h) => t + n(h.amount_inr), 0) - a.own;
    a.frozenExtra = a.freeze ? Math.max(0, n(a.freeze.amount_inr) - a.lien) : 0;
    a.first = [...a.liens, a.freeze, a.marker].filter(Boolean).sort((x, y) => String(x.placed_at).localeCompare(String(y.placed_at)))[0];
    a.since = watch.filter((e) => e.account_id === a.id && KEY_EVENTS.includes(e.event_kind));
    a.kind = a.freeze ? "frozen" : a.liens.length ? "hold" : "watched";
  }
  accounts.sort((x, y) => (y.decision ? 1 : 0) - (x.decision ? 1 : 0) || (x.kind === "watched") - (y.kind === "watched") || y.held - x.held);
  const held = accounts.filter((a) => a.kind !== "watched");
  const total = held.reduce((t, a) => t + a.held, 0);
  const victims = held.reduce((t, a) => t + a.victimMoney, 0);
  const own = held.reduce((t, a) => t + a.own, 0);
  const frozenExtra = held.reduce((t, a) => t + a.frozenExtra, 0);
  const complaints = new Set(held.flatMap((a) => a.victims.map((h) => h.case_id))).size;
  const ownNames = held.filter((a) => a.own).map((a) => `${shortName(a.name)}'s`);
  const freezes = held.filter((a) => a.freeze && a.frozenExtra);
  const watchedOnly = accounts.filter((a) => a.kind === "watched");
  const decisions = accounts.filter((a) => a.decision).length;
  const FILTERS = [["all", "All", () => true], ["waiting", "Decision waiting", (a) => a.decision], ["own", "Has a customer's own money", (a) => a.own > 0],
    ["frozen", "Frozen", (a) => a.freeze], ["watched", "Watched only", (a) => a.kind === "watched"]];
  const rows = accounts.filter(FILTERS.find((f) => f[0] === filter)[2]);
  const pct = (x) => `${total ? (x / total) * 100 : 0}%`;
  return (
    <main id="main" className="wrap" style={{ paddingTop: 24, paddingBottom: 40 }}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-end", gap: "12px 24px", marginBottom: 18 }}>
        <div>
          <h1 style={{ fontSize: 28, lineHeight: 1.2 }}>{inrR(total)} held across {plural(held.length, "account")}</h1>
          <p style={{ color: "#44505e", marginTop: 4 }}>Every hold at Bank X today, and whose money is in it.{watchedOnly.length ? ` ${watchedOnly.length} more account${watchedOnly.length === 1 ? " is" : "s are"} watched with nothing held.` : ""}</p>
        </div>
        {decisions > 0 && <a className="chip" href="#/review" style={{ background: "#fbf0dc", borderColor: "#e3c58e", color: "#3d2600" }}>{plural(decisions, "hold")} {decisions === 1 ? "has" : "have"} a decision waiting</a>}
      </div>
      {held.length > 0 && (
        <section className="card" style={{ padding: "18px 20px", marginBottom: 22 }} aria-labelledby="mix-h">
          <h2 id="mix-h" style={{ fontSize: 17, marginBottom: 10 }}>Whose money is held</h2>
          <div className="bar" role="img" aria-label={`${inrR(victims)} victims' money, ${inrR(own)} genuine customers' own money, ${inrR(frozenExtra)} officer-ordered freeze`}>
            <div style={{ flex: `0 0 ${pct(victims)}`, background: "#5b3fa0" }}>Victims' money {inrR(victims)}</div>
            {own > 0 && <div style={{ flex: `0 0 ${pct(own)}`, background: "#2f7a5a" }} />}
            {frozenExtra > 0 && <div style={{ flex: "1 1 auto", background: "#2a3744" }} />}
          </div>
          <div style={{ marginTop: 10 }}>
            <div className="key"><span className="sw" style={{ background: "#5b3fa0" }} /><span><b>Victims' money</b>, traced from {plural(complaints, "complaint")}. This is what the holds are for.</span><b>{inrR(victims)}</b></div>
            {own > 0 && <div className="key"><span className="sw" style={{ background: "#2f7a5a" }} /><span><b>Genuine customers' own money</b> in {joinAnd(ownNames)} hold{ownNames.length === 1 ? "" : "s"}. Released if you approve the {plural(ownNames.length, "smaller hold")}.</span><b>{inrR(own)}</b></div>}
            {freezes.map((a) => <div key={a.id} className="key"><span className="sw" style={{ background: "#2a3744" }} /><span><b>Freeze ordered by {a.freeze.placed_by}</b> on {shortName(a.name)}.{a.victims.length ? " Beyond the traced amount, so it is nobody's money in the totals." : " Not traced from any complaint, so it is nobody's money in the totals."}</span><b>{inrR(a.frozenExtra)}</b></div>)}
          </div>
        </section>
      )}
      <nav aria-label="Filter holds" className="chips" style={{ marginBottom: 12 }}>
        {FILTERS.map(([k, label, fn]) => <button key={k} type="button" className={`chip${filter === k ? " on" : ""}`} aria-current={filter === k ? "true" : undefined} onClick={() => setFilter(k)}>{label} <span>{accounts.filter(fn).length}</span></button>)}
      </nav>
      {accounts.length === 0 ? <p className="empty">No holds yet.</p> : (
        <div className="card" style={{ overflowX: "auto" }}>
          <table className="t">
            <thead><tr><th scope="col">Account</th><th scope="col">Hold</th><th scope="col" className="r">Held</th><th scope="col">Victims' money</th><th scope="col" className="r">Customer's own</th><th scope="col">Since the hold</th><th scope="col">Next</th></tr></thead>
            <tbody>
              {rows.map((a) => {
                const sub = a.name && a.name.includes("(") ? ` · ${a.name.replace(/^.*\((.*)\)$/, "$1")}` : "";
                return (
                  <tr key={a.id} style={a.kind === "watched" ? { background: "#fafbfc" } : undefined}>
                    <td>{a.kind === "watched" ? <b>{shortName(a.name)}</b> : <a href={`#/record/${encodeURIComponent(a.id)}`}><b>{accountName(a.name, a.id).replace(/ \(.*\)$/, "")}</b></a>}<br /><span className="id">{a.id}{sub}</span></td>
                    <td>{a.kind === "frozen" ? <span className="pill p-frz">Frozen</span> : a.kind === "watched" ? <span className="pill p-plain">Watched</span> : <span className="pill p-held">Hold</span>}<br />
                      <span className="note">{clock(a.first?.placed_at)}{a.kind === "frozen" ? `, ${a.freeze.placed_by}` : ""}</span></td>
                    <td className="r"><b>{inrR(a.held)}</b></td>
                    <td>{a.victims.length ? a.victims.map((h) => <div key={h.hold_id}><span className="vic">{inrR(n(h.amount_inr) - (a.decision?.case_id === h.case_id ? a.own : 0))}</span> {victim(h.hold_tag)}</div>)
                      : a.unlinked.length ? <span className="note">{inrR(a.lien)} caught, not linked to a victim yet</span>
                        : a.kind === "watched" ? <span className="note">Nothing left when the complaint came</span> : <span className="note">None traced</span>}</td>
                    <td className={`r${a.own ? " own" : ""}`}>{a.kind === "frozen" && !a.victims.length ? <span className="note">Not counted</span> : inrR(a.own)}</td>
                    <td>{a.since.length ? <>{a.since.slice(0, 2).map((e) => <div key={e.event_id}><span className="mono">{clock(e.event_at)}</span> {shortEvent(e)}</div>)}
                      {a.since.length > 2 && <a className="note" href={`#/record/${encodeURIComponent(a.id)}`}>+ {a.since.length - 2} more</a>}</> : <span className="note">Nothing</span>}</td>
                    <td>{a.decision ? (
                      <a className="pill p-wait" href="#/review" style={a.decision.action_code === "PROPOSE_NARROW" ? { border: "2px solid #2f7a5a", color: "#1f5a41" } : { border: "2px solid #2a3744" }}>
                        {a.decision.action_code === "PROPOSE_NARROW" ? `Reduce hold to ${inrR(a.decision.amount_inr)}?` : a.decision.action_code === "PROPOSE_FULL_FREEZE" ? "Freeze account?" : a.decision.action_code === "PROPOSE_RELEASE" ? "Release the hold?" : "Accept the near match?"}</a>
                    ) : <span className="note">{a.kind === "frozen" ? "Police reply" : a.kind === "watched" ? "Holds itself if money arrives from a held account" : "Nothing to do"}</span>}</td>
                  </tr>
                );
              })}
              {filter === "all" && held.length > 0 && (
                <tr><td><b>Total</b></td><td /><td className="r"><b>{inrR(total)}</b></td><td><span className="vic">{inrR(victims)}</span></td><td className="r own">{inrR(own)}</td>
                  <td className="note">{freezes.length ? `+ ${inrR(frozenExtra)} frozen on ${joinAnd(freezes.map((a) => shortName(a.name)))}` : ""}</td><td /></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      <p className="note" style={{ marginTop: 10 }}>Sorted with decisions first, then by size. Every account opens its hold record.</p>
      <p className="note">Amber always means waiting for you. The edge tells the two apart: green for reducing a hold, which releases a customer's own money, and dark for freezing the whole account. The wording differs too.</p>
    </main>
  );
}

// ---------------------------------------------------------------- Audit log (design board "Audit log + handover note")
const WHO = { system: ["The system", "p-system"], watch: ["After the hold", "p-watch"], note: ["The system", "p-system"] };

function HandoverNote({ log }) {
  const lines = String(log.text || "").split("\n").filter(Boolean);
  const blocks = [];
  for (const l of lines.slice(1)) {
    if (l.startsWith("## ")) blocks.push({ h: rs(l.slice(3)), items: [] });
    else if (l.startsWith("- ")) (blocks.at(-1) || blocks[blocks.push({ h: null, items: [] }) - 1]).items.push(rs(l.slice(2)));
  }
  const strong = (t) => {
    const m = /^([^.]+, \d\d:\d\d, ₹[\d,]+\.)(.*)$/.exec(t) || /^([^:]+:)(.*)$/.exec(t);
    return m ? <><b>{m[1]}</b>{m[2]}</> : t;
  };
  return (
    <section className="card hn handover" style={{ padding: "22px 24px", maxWidth: 970, marginBottom: 26 }} aria-labelledby="hn-h">
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "baseline", gap: "8px 16px" }}>
        <h2 id="hn-h" style={{ fontSize: 20 }}>Handover note for the morning desk</h2>
        <span className="pill p-plain" style={{ fontSize: 12 }}>Filed {clock(log.period_to)} · covers {clock(log.period_from)} to {clock(log.period_to)}</span>
      </div>
      <p className="note" style={{ marginTop: 4 }}>A record of the night as it stood at {clock(log.period_to)}. Figures have moved since. For the live count, see <a href="#/review">Review</a>.</p>
      <p style={{ fontSize: 15, fontWeight: 700, marginTop: 14 }}>{rs(lines[0])}</p>
      {blocks.map((b, i) => (
        <div key={i}>{b.h && <h3>{b.h}</h3>}<ul style={b.h ? undefined : { marginTop: 6 }}>{b.items.map((t, j) => <li key={j}>{strong(t)}</li>)}</ul></div>
      ))}
      <div className="acts" style={{ display: "flex", gap: 8, marginTop: 18 }}>
        <a className="btn" href="#/review">Go to Review</a>
        <button className="btn" type="button" onClick={() => { document.body.classList.add("printing-note"); window.print(); setTimeout(() => document.body.classList.remove("printing-note"), 500); }}>Download as PDF</button>
      </div>
    </section>
  );
}

export function Audit({ caseId }) {
  const story = useLoad(() => api.story(), []);
  const inbox = useLoad(() => api.inbox().catch(() => null), []);
  const [filter, setFilter] = useState("all");
  const [complaint, setComplaint] = useState(caseId || "all");
  // wait for the handover note too, so the page does not jump when it arrives
  if ((story.loading && !story.data) || (inbox.loading && !inbox.data)) return <div className="wrap"><Loading what="Loading the audit log" /></div>;
  if (story.error) return <div className="wrap"><Failed error={story.error} retry={story.reload} /></div>;
  const all = story.data;
  const FILTERS = [["all", "All", () => true], ["officer", "Officer decisions", (e) => e.kind === "officer"], ["system", "System steps", (e) => e.kind === "system" || e.kind === "note"], ["watch", "After the holds", (e) => e.kind === "watch"]];
  const complaints = [...new Map(all.filter((e) => e.case_id).map((e) => [e.case_id, `${e.label}${e.victim ? `, ${e.victim}` : ""}`])).entries()];
  const byComplaint = all.filter((e) => complaint === "all" || e.case_id === complaint);
  const rows = byComplaint.filter(FILTERS.find((f) => f[0] === filter)[2]);
  const log = inbox.data?.morning_log;
  return (
    <main id="main" className="wrap" style={{ paddingTop: 24, paddingBottom: 40 }}>
      <h1 style={{ fontSize: 30 }}>Audit log</h1>
      <p style={{ fontSize: 15, color: "#44505e", marginTop: 4, marginBottom: 22 }}>Every step the system took and every decision an officer signed, in order. Nothing here can be edited.</p>
      {log && <HandoverNote log={log} />}
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: "10px 16px", marginBottom: 12 }}>
        <div className="chips">{FILTERS.map(([k, label, fn]) => <button key={k} type="button" className={`chip${filter === k ? " on" : ""}`} onClick={() => setFilter(k)}>{label} <span>{byComplaint.filter(fn).length}</span></button>)}</div>
        <label className="note" style={{ display: "flex", alignItems: "center", gap: 8 }}>Complaint
          <select value={complaint} onChange={(e) => setComplaint(e.target.value)}><option value="all">All {complaints.length}</option>{complaints.map(([id, l]) => <option key={id} value={id}>{l}</option>)}</select></label>
      </div>
      <div className="card" style={{ overflowX: "auto" }}>
        <table className="t">
          <thead><tr><th scope="col">Time</th><th scope="col">Who</th><th scope="col">What happened</th><th scope="col">Complaint</th><th scope="col">Reference</th></tr></thead>
          <tbody>
            {rows.map((e, i) => {
              const [who, cls] = e.kind === "officer" ? [e.who, "p-officer"] : WHO[e.kind] || [e.who, "p-system"];
              return (
                <tr key={`${e.ref}-${i}`}>
                  <td className="mono">{clock(e.t)}</td>
                  <td><span className={`pill ${cls}`}>{who}</span></td>
                  <td>{rs(e.what)}</td>
                  <td className="note">{e.case_id ? (e.victim || e.label) : e.kind === "note" ? "All" : "None"}</td>
                  <td className="id">{e.ref}</td>
                </tr>
              );
            })}
            {!rows.length && <tr><td colSpan={5} className="note">Nothing in this view.</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="note" style={{ marginTop: 10 }}>Showing {rows.length} of {all.length}. Each row carries the officer's name or "the system", never a free-text name.</p>
    </main>
  );
}