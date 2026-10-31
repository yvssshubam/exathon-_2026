import { useContext, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, SNAPSHOT } from "../api.js";
import DecisionCard, { attemptsOf, balanceChanged, clearCase, figures, KIND, matchCount, Undo } from "../components/DecisionCard.jsx";
import { Failed, Loading, MoreButton } from "../components/bits.jsx";
import { ComplaintLink, Complaints, HeldOvernight, ProactiveQueue, ReviewAccounts, useRowClass } from "../components/Proactive.jsx";
import SidePanel, { PanelFooter } from "../components/SidePanel.jsx";
import { clock, dayTime, duration, inrR, joinAnd, plural, rs, shortName, victim } from "../format.js";
import { useShowManual } from "../demo.js";
import { useLoad } from "../hooks.js";

// Review, the officer's home (design board "Review (home)" and "Decisions at volume"). The dark band answers
// "how did the day go"; the decisions for the officer come first, ordered by urgency (nothing held yet, then
// genuine customers locked out of their own money, then freezes, where the money is already held); today's
// complaints, what happened since the holds and the KYC packets sit on the right.

const GROUPS = [
  { id: "unmatched", code: "UNMATCHED_NOTICE", title: "No matching payment", jump: "No matching payment", tile: "Complaint with no matching payment",
    pill: ["Act first: nothing is held", "p-urgent"], badge: { background: "#f9e1df", color: "#7a211b" } },
  { id: "narrow", code: "PROPOSE_NARROW", title: "Reduce a genuine customer's hold", jump: "Reduce the hold", tile: "Reduce a genuine customer's hold",
    pill: ["Customers locked out of their own money", "p-wait"], badge: { background: "#fbf0dc", color: "#3d2600" },
    desc: "These accounts got fraud money in a payment that looks like an ordinary sale. The rules made the system hold the whole payment; only part of it is the victim's." },
  { id: "freeze", code: "PROPOSE_FULL_FREEZE", title: "Freeze a full account", jump: "Freeze the account", tile: "Freeze a full account",
    pill: ["The victim's money is already held", "p-plain"], badge: { background: "#e8ecf0", color: "#44505e" },
    desc: "A freeze adds one thing to the hold: it blocks money that comes in later, until the police decide. Suggested when fraud money leaves an account faster than its other money, in three or more fraud cases, or when a pass-through account tries to cash out." },
  { id: "larger", code: "PROPOSE_LARGER_HOLD", title: "Hold more on an account", jump: "Hold more", tile: "Hold more on an account",
    pill: ["Fraud money leaves fast", "p-wait"], badge: { background: "#fbf0dc", color: "#3d2600" },
    desc: "Fraud-linked money leaves these accounts faster than their other money, in two fraud cases: a larger hold, up to the fraud-linked money of 90 days and never above the balance. Not the whole account." },
  { id: "release", code: "PROPOSE_RELEASE", title: "Release a hold", jump: "Release", tile: "Release a hold", pill: ["No police direction in 75 days", "p-plain"],
    badge: { background: "#e8ecf0", color: "#44505e" }, desc: "A hold with no police direction for 75 days must be lifted or confirmed." },
];
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const n = (v) => Number(v || 0);
const minutesBetween = (a, b) => Math.round((new Date(String(b).replace(" ", "T")) - new Date(String(a).replace(" ", "T"))) / 60000);
const possessive = (s) => (s.endsWith("s") ? `${s}'` : `${s}'s`);

export default function Review({ officer, onChange }) {
  const inbox = useLoad(() => api.inbox(), []);
  const day = useLoad(() => api.day(), []);
  const clockNow = useLoad(() => api.clock(), []);
  const pro = useLoad(() => api.proactive().catch(() => null), []);
  const [justDone, setJustDone] = useState([]);       // decided this visit: rows stay in place as one grey line
  const [panelId, setPanelId] = useState(null);       // the decision open in the side panel (one for the whole page)
  const [focus, setFocus] = useState(null);           // the complaint jumped to from Today's complaints

  if (inbox.loading && !inbox.data) return <div className="wrap"><Loading what="Loading the morning" /></div>;
  if (inbox.error) return <div className="wrap"><Failed error={inbox.error} retry={inbox.reload} /></div>;
  const d = inbox.data;
  const decidedById = Object.fromEntries((d.decided || []).map((p) => [p.proposal_id, p]));
  const onDone = (p) => { setJustDone((j) => [...j, p]); inbox.reload(); day.reload(); pro.reload(); onChange?.(); };
  const reload = () => { inbox.reload(); day.reload(); pro.reload(); onChange?.(); };
  const overnight = pro.data?.held_overnight?.length || 0, due = pro.data?.queue?.length || 0, review = pro.data?.review?.length || 0;
  // every complaint that needs the officer is in this list: the ones the system could not complete, and any waiting
  // proposal whose kind has no group of its own (a safety net, so nothing counted for the officer is ever missing)
  const failedRuns = (d.done || []).filter((t) => ["FAILED", "CHECK_FAILED"].includes(t.run_status));
  const byCase = Object.fromEntries((d.done || []).map((t) => [t.case_id, t]));
  const who = (caseId) => { const t = byCase[caseId]; return t ? `${victim(t.victim_tag)}, ${clock(t.received_at)}` : null; };
  const known = new Set(GROUPS.map((g) => g.code));
  const others = d.waiting.filter((p) => !known.has(p.action_code));
  // waiting items, plus the ones decided this visit (kept where they were, from the snapshot taken before deciding)
  const items = [...d.waiting, ...justDone.filter((p) => !d.waiting.some((w) => w.proposal_id === p.proposal_id))];
  const groups = GROUPS.map((g) => ({ ...g, items: items.filter((p) => p.action_code === g.code), waiting: d.waiting.filter((p) => p.action_code === g.code) }));
  const total = d.waiting.length + overnight + due + review + failedRuns.length;
  // every link to a group closes the side panel first, then scrolls there
  const jump = (id, caseId = null) => { setPanelId(null); setFocus(caseId); document.getElementById(`g-${id}`)?.scrollIntoView({ behavior: "smooth" }); };

  return (
    <Complaints.Provider value={{ who, focus }}>
      <Band d={d} day={day.data} now={clockNow.data?.clock} groups={groups} total={total} jump={jump} overnight={overnight} due={due} review={review} />
      <main id="main" className="wrap" style={{ display: "flex", flexWrap: "wrap", gap: 28, alignItems: "flex-start", paddingTop: 24, paddingBottom: 40 }}>
        <section style={{ flex: "999 1 640px", minWidth: 0 }} aria-labelledby="dec-h">
          <div style={{ position: "sticky", top: 0, zIndex: 2, background: "#f3f5f7", padding: "4px 0 12px" }}>
            <h2 id="dec-h" style={{ fontSize: 20, marginBottom: 10 }}>Decisions for you <span style={{ color: "#5b6675", fontWeight: 400 }}>{total}</span></h2>
            <nav className="jump" aria-label="Jump to a group" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {failedRuns.length > 0 && <a href="#g-failed" onClick={(e) => { e.preventDefault(); jump("failed"); }}>Not completed <b style={{ background: "#f9e1df", color: "#7a211b" }}>{failedRuns.length}</b></a>}
              {overnight > 0 && <a href="#g-overnight" onClick={(e) => { e.preventDefault(); jump("overnight"); }}>Held overnight <b style={{ background: "#e3eefa", color: "#16406b" }}>{overnight}</b></a>}
              {review > 0 && <a href="#g-review" onClick={(e) => { e.preventDefault(); jump("review"); }}>Review an account <b style={{ background: "#fbf0dc", color: "#3d2600" }}>{review}</b></a>}
              {groups.filter((g) => g.waiting.length).map((g) => (
                <a key={g.id} href={`#g-${g.id}`} onClick={(e) => { e.preventDefault(); jump(g.id); }}>
                  {g.jump} <b style={g.badge}>{g.waiting.length}</b></a>
              ))}
              {others.length > 0 && <a href="#g-other" onClick={(e) => { e.preventDefault(); jump("other"); }}>Other decisions <b style={{ background: "#e8ecf0", color: "#44505e" }}>{others.length}</b></a>}
              {due > 0 && <a href="#g-clock" onClick={(e) => { e.preventDefault(); jump("clock"); }}>Decisions due <b style={{ background: "#fbf0dc", color: "#3d2600" }}>{due}</b></a>}
            </nav>
          </div>
          {total === 0 && justDone.length === 0 && <p className="card" style={{ padding: "14px 18px", marginTop: 12 }}>Nothing is waiting for you.{d.done.length ? ` All ${plural(d.done.length, "complaint")} today are handled.` : " No complaints have come in yet."}</p>}
          <FailedRuns list={failedRuns} onDone={reload} openId={panelId} setOpenId={setPanelId} />
          <HeldOvernight data={pro.data} officer={officer} onDone={reload} openId={panelId} setOpenId={setPanelId} />
          <ReviewAccounts data={pro.data} officer={officer} onDone={reload} openId={panelId} setOpenId={setPanelId} />
          {groups.filter((g) => g.items.length).map((g) => (
            <Group key={g.id} g={g} d={d} officer={officer} onDone={onDone} reload={reload} decidedById={decidedById}
              openId={panelId} setOpenId={setPanelId} />
          ))}
          <OtherDecisions items={others} d={d} officer={officer} onDone={onDone} openId={panelId} setOpenId={setPanelId} />
          <ProactiveQueue data={pro.data} officer={officer} onDone={reload} openId={panelId} setOpenId={setPanelId} />
        </section>
        <aside style={{ flex: "1 1 360px", minWidth: 0 }} aria-labelledby="today-h">
          <TodayList d={d} groups={groups} onJump={jump} pro={pro.data} />
          <Kyc d={d} />
        </aside>
      </main>
    </Complaints.Provider>
  );
}

// ---------------------------------------------------------------- the band
function Band({ d, day, now, groups, total, jump, overnight = 0, due = 0, review = 0 }) {
  const showManual = useShowManual();
  const done = d.done || [];
  const failed = done.filter((t) => ["FAILED", "CHECK_FAILED"].includes(t.run_status));
  const night = done.filter((t) => t.after_hours);
  const ts = now || done.at(-1)?.received_at;
  const date = ts ? new Date(String(ts).slice(0, 10)) : null;
  const top = date ? `${DAYS[date.getDay()].slice(0, 3)} ${date.getDate()} ${MONTHS[date.getMonth()]}${now ? ` · ${clock(now)}` : ""}` : "";
  let headline;
  if (!done.length) headline = "No complaints yet today.";
  else if (failed.length) headline = `${plural(done.length, "complaint")} today. ${failed.length} could not be completed by the system and ${failed.length === 1 ? "needs" : "need"} you.`;
  else headline = `${plural(done.length, "complaint")} received and closed`;
  const narrowWaiting = groups.find((g) => g.id === "narrow").waiting.length;
  const tool = day?.tool, today = day?.today;
  const pct = (a, b) => (n(b) ? Math.round((n(a) / n(b)) * 100) : 0);
  const nightHeld = night.filter((t) => t.sim_holds_at);
  const nightMins = today?.night_minutes || [];
  const frozen = (today?.frozen_names || []).map((x) => possessive(shortName(x)));
  const w = d.watch || [];
  const refused = w.filter((e) => ["CASHOUT_BLOCKED", "TRANSFER_BLOCKED"].includes(e.event_kind));
  const caught = w.filter((e) => ["NEW_MONEY_HELD", "PROACTIVE_HOLD"].includes(e.event_kind));
  const linkedVictims = new Set(w.filter((e) => e.event_kind === "LINKED_TO_VICTIM").map((e) => e.case_id));
  const watched = w.filter((e) => e.event_kind === "RECIPIENT_WATCHED");
  return (
    <section style={{ background: "#1c2742", color: "#e4e9f5" }} aria-labelledby="band-h">
      <div className="wrap" style={{ paddingTop: 14, paddingBottom: 14 }}>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-start", gap: "8px 24px", marginBottom: 12 }}>
          <div style={{ maxWidth: 860 }}>
            <p style={{ fontSize: 12, letterSpacing: "0.05em", textTransform: "uppercase", color: "#a9b6d6", marginBottom: 2 }}>{top}</p>
            <h1 id="band-h" style={{ fontSize: 20, lineHeight: 1.3, color: "#fff", marginBottom: 4 }}>{headline}</h1>
          </div>
          {d.morning_log && <a className="btn btn-s" href="#/audit" style={{ background: "transparent", color: "#fff", borderColor: "#5b6a93" }}>Handover note, {clock(d.morning_log.period_to)}</a>}
        </div>
        {done.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
            <Tile title="Victims' money secured" big={tool ? inrR(tool.victim_held_inr) : "…"} sub={day ? `${pct(tool.victim_held_inr, day.victim_paid_inr)}% of ${inrR(day.victim_paid_inr)} reported` : ""}
              manual={today && <>Manual process: <b>{inrR(today.victim_held_inr)}</b> ({pct(today.victim_held_inr, day.victim_paid_inr)}%)</>} />
            <Tile title="Genuine customers' money blocked" big={tool ? inrR(tool.honest_locked_now_inr) : "…"} green
              sub={narrowWaiting && n(tool?.honest_locked_now_inr) ? `₹0 once you approve ${plural(narrowWaiting, "smaller hold")}` : n(tool?.honest_locked_now_inr) ? "" : "None of their own money is held"}
              manual={today && <>Manual process: <b>{inrR(today.honest_locked_inr)}</b>{frozen.length ? `, ${joinAnd(frozen)} full account${frozen.length === 1 ? "" : "s"} frozen` : ""}</>} />
            <Tile title="First hold" big={nightHeld.length ? duration(Math.max(...nightHeld.map((t) => minutesBetween(t.received_at, t.sim_holds_at)))) : "–"}
              sub={nightHeld.length ? `at ${joinAnd(nightHeld.map((t) => clock(t.received_at)))}` : "No complaint needed a hold"}
              manual={nightMins.length > 0 && <>Manual process: <b>{Math.min(...nightMins) === Math.max(...nightMins) ? duration(Math.min(...nightMins)) : `${duration(Math.min(...nightMins))} to ${duration(Math.max(...nightMins))}`}</b></>} />
          </div>
        )}
        {w.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: "4px 14px", marginTop: 10, fontSize: 13, color: "#c4cde3" }}>
            <b style={{ color: "#fff" }}>Since the holds:</b>
            {[refused.length && `${plural(refused.length, "cash-out")} refused, ${inrR(refused.reduce((t, e) => t + n(e.amount_inr), 0))} kept`,
              caught.length && `${inrR(caught.reduce((t, e) => t + n(e.amount_inr), 0))} of new money caught${linkedVictims.size ? `, linked to ${plural(linkedVictims.size, "victim")}` : ""}`,
              watched.length && `${plural(watched.length, "new account")} watched`].filter(Boolean).flatMap((x, i) => (i ? [<span key={`s${i}`} aria-hidden="true">·</span>, <span key={i}>{x}</span>] : [<span key={i}>{x}</span>]))}
            <a href="#/audit" style={{ color: "#fff" }}>All {plural(w.length, "event")}</a>
          </div>
        )}
        {done.length > 0 && showManual && <p className="demo-text" style={{ marginTop: 6, fontSize: 11.5 }}>"Manual process" replays today's practice on the same transactions: the desk opens at 10:00 and closes at 18:00, takes about 20 minutes for the first account and 15 for each next one, and freezes whole accounts.{done.some((t) => t.run_status === "SENT_BACK") ? ` The ${joinAnd(done.filter((t) => t.run_status === "SENT_BACK").map((t) => clock(t.received_at)))} complaint is left out of both: nothing matched it.` : ""}</p>}
      </div>
    </section>
  );
}

function Tile({ title, big, sub, manual, green }) {
  const showManual = useShowManual();
  return (
    <div style={{ background: "#26335a", borderRadius: 10, padding: "10px 14px" }}>
      <p style={{ fontSize: 13, color: "#c4cde3" }}>{title}</p>
      <p style={{ fontSize: 22, fontWeight: 700, color: "#fff", lineHeight: 1.25 }}>{big}</p>
      {sub && <p style={{ fontSize: 13, color: green ? "#b9dcc9" : "#c4cde3", marginTop: 2 }}>{sub}</p>}
      {manual && showManual && <p className="demo-text" style={{ fontSize: 12, marginTop: 6, paddingTop: 6, borderTop: "1px solid #34437a" }}>{manual}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- a decision group: cards up to 3, a list from 4
function Group({ g, d, officer, onDone, reload, decidedById, openId, setOpenId }) {
  const list = true;              // every group is a list: a row per decision, its details in the side panel
  const one = g.items.length === 1 && g.id === "unmatched";
  const desc = g.id === "unmatched"
    ? one ? `The UTR on the notice is not a payment into this account, so nothing was held and the notice went back to the police at ${clock(g.items[0].proposed_at)}. A payment that fits everything else did arrive, and it is still in the account.`
      : "The UTRs on these notices are not payments into the accounts they name, so nothing was held and the notices went back to the police. Payments that fit everything else did arrive."
    : g.desc;
  return (
    <div id={`g-${g.id}`} style={{ marginTop: g.id === "unmatched" ? 12 : 26, scrollMarginTop: 110 }}>
      {!list && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 10px", marginBottom: 4 }}>
            <h3 style={{ fontSize: 17 }}>{g.title}</h3><span className={`pill ${g.pill[1]}`}>{g.pill[0]}</span>
          </div>
          <p style={{ fontSize: 14, color: "#44505e", marginBottom: 12, maxWidth: 760 }}>{desc}</p>
          {(g.id === "narrow" ? [...g.items].sort((a, b) => figures(b).release - figures(a).release) : g.items).map((p) => {
            const done = decidedById[p.proposal_id];
            return done ? <DoneLine key={p.proposal_id} p={done} officer={officer} onDone={reload} card /> :
              <DecisionCard key={p.proposal_id} p={p} officer={officer} onDone={onDone} watch={d.watch} />;
          })}
        </>
      )}
      {list && <ListGroup g={g} d={d} officer={officer} onDone={onDone} reload={reload} decidedById={decidedById}
        openId={openId} setOpenId={setOpenId} />}
    </div>
  );
}

// one grey line for a row decided this visit: who decided and when, and the undo for that kind
function DoneLine({ p, officer, onDone, card, cols }) {
  const outcome = p.decision_outcome || p.proposal_status;
  const word = outcome === "REJECTED" ? (p.action_code === "UNMATCHED_NOTICE" ? "Left with the police" : p.action_code === "PROPOSE_PRE_ALERT" ? "Not sent" : `Kept at ${inrR(p.current_hold_inr || p.amount_inr)}`)
    : { PROPOSE_NARROW: `Reduced to ${inrR(p.decided_amount_inr ?? p.amount_inr)}`, PROPOSE_FULL_FREEZE: "Frozen", UNMATCHED_NOTICE: `Accepted, ${inrR(p.amount_inr)} held`, PROPOSE_RELEASE: "Released", PROPOSE_PRE_ALERT: "Pre-alert sent" }[p.action_code];
  const body = <><span style={{ color: "#5b6675" }}>{word} by {p.decided_by}, {clock(p.decided_at)}</span><Undo p={p} officer={officer} onDone={onDone} compact /></>;
  if (cols) return <tr className="done"><td colSpan={cols - 1}><b style={{ color: "#5b6675" }}>{shortName(p.name)}</b> <span style={{ marginLeft: 16 }}>{word} by {p.decided_by}, {clock(p.decided_at)}</span></td><td className="r"><Undo p={p} officer={officer} onDone={onDone} compact /></td></tr>;
  return <div className={card ? "card" : ""} style={{ padding: "14px 18px", marginBottom: 14, display: "flex", flexWrap: "wrap", gap: "8px 16px", alignItems: "center", background: "#fafbfc" }}>
    <b style={{ color: "#5b6675" }}>{shortName(p.name)}</b>{body}</div>;
}

function ListGroup({ g, d, officer, onDone, reload, decidedById, openId, setOpenId }) {
  const rowClass = useRowClass();
  const [filter, setFilter] = useState("all");
  const [complaint, setComplaint] = useState("all");
  const [more, setMore] = useState(false);
  const [picked, setPicked] = useState([]);
  const [signing, setSigning] = useState(false);
  const listRef = useRef(null);                       // the side panel keeps clear of this list's Open buttons
  const rows = useMemo(() => {
    let r = [...g.items];
    if (g.id === "narrow") r.sort((a, b) => figures(b).release - figures(a).release);
    if (g.id === "freeze" || g.id === "larger") r.sort((a, b) => attemptsOf(b, d.watch).length - attemptsOf(a, d.watch).length || n(b.fraud_case_count) - n(a.fraud_case_count) || n(b.current_hold_inr) - n(a.current_hold_inr));
    if (g.id === "unmatched") r.sort((a, b) => n(b.balance_today_inr) - n(a.balance_today_inr));
    return r;
  }, [g.items, g.id, d.watch]);
  const isClear = (p) => clearCase(p)[0] && !balanceChanged(p);
  let shown = rows.filter((p) => complaint === "all" || p.case_id === complaint);
  if (g.id === "narrow" && filter !== "all") shown = shown.filter((p) => (filter === "clear") === isClear(p));
  const limited = more ? shown : shown.slice(0, 10);
  const complaints = [...new Map(rows.map((p) => [p.case_id, p.label])).entries()];
  // the side panel shows one waiting decision from this group, or none
  const openP = rows.find((p) => p.proposal_id === openId && !decidedById[p.proposal_id]) || null;
  const open = openP?.proposal_id ?? null;
  const waitingRows = rows.filter((p) => !decidedById[p.proposal_id]);
  const own = waitingRows.reduce((t, p) => t + figures(p).release, 0);
  const sel = picked.map((id) => rows.find((p) => p.proposal_id === id)).filter((p) => p && !decidedById[p.proposal_id] && isClear(p));
  const nextAfter = (p) => rows.find((x) => x.proposal_id !== p.proposal_id && !decidedById[x.proposal_id] && !isClear(x))?.proposal_id ?? null;
  const decide = (p) => { setOpenId(nextAfter(p)); onDone(p); };

  const title = { narrow: "Reduce a genuine customer's hold", freeze: "Freeze the whole account?", larger: "Hold more on an account?", unmatched: "Notice does not fully match", release: "Release a hold" }[g.id];
  const cols = g.id === "narrow" ? 8 : g.id === "freeze" || g.id === "larger" ? 6 : 4;
  const bulk = g.id === "narrow" && rows.length >= 4;        // ticking several to approve together, as before
  return (
    <section className="card" style={{ padding: "18px 20px", ...(g.id === "unmatched" ? { borderColor: "#e7b3ae" } : {}) }} aria-labelledby={`lg-${g.id}`}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: "10px 16px", marginBottom: 12 }}>
        <div>
          <h3 id={`lg-${g.id}`} style={{ fontSize: 20 }}>{title} · {waitingRows.length}</h3>
          {g.id === "narrow" && <p className="note" style={{ fontSize: 14 }}>{inrR(own)} of their own money is blocked. The victims' share stays held whatever you decide.</p>}
          {g.id === "unmatched" && <p style={{ color: "#7a211b", fontWeight: 600 }}>Nothing is held on any of these yet</p>}
          {g.id !== "narrow" && g.id !== "unmatched" && g.desc && <p className="note" style={{ fontSize: 14, maxWidth: 760 }}>{g.desc}</p>}
        </div>
        {g.id === "narrow" && (
          <div className="chips">
            {[["all", "All", rows.length], ["clear", "Ready to approve", rows.filter(isClear).length], ["look", "Needs review", rows.filter((p) => !isClear(p)).length]].map(([k, l, c]) => (
              <button key={k} className={`chip${filter === k ? " on" : ""}`} type="button" onClick={() => { setOpenId(null); setFilter(k); }}>{l} <span>{c}</span></button>
            ))}
          </div>
        )}
        {rows.length > 10 && (
          <label className="note" style={{ display: "flex", alignItems: "center", gap: 8 }}>Complaint
            <select value={complaint} onChange={(e) => { setOpenId(null); setComplaint(e.target.value); }}><option value="all">All</option>{complaints.map(([id, l]) => <option key={id} value={id}>{l}</option>)}</select></label>
        )}
      </div>
      {signing ? <SignStep list={sel} officer={officer} onBack={() => setSigning(false)} onSigned={() => { setSigning(false); setPicked([]); reload(); }} /> : (
        <>
          <div className="tw" ref={listRef}>
            <table className="t">
              <thead><tr>
                {g.id === "narrow" && <><th scope="col">{bulk ? <span className="sr">Select</span> : null}</th><th scope="col">Customer</th><th scope="col" className="r">Held</th><th scope="col" className="r">Victim's share</th><th scope="col" className="r">Would release</th><th scope="col">Complaint</th><th scope="col">Case</th><th /></>}
                {g.id === "freeze" && <><th scope="col">Account</th><th scope="col">Complaint</th><th scope="col" className="r">Fraud cases</th><th scope="col" className="r">Held</th><th scope="col">Tried to move held money</th><th /></>}
                {g.id === "larger" && <><th scope="col">Account</th><th scope="col">Complaint</th><th scope="col" className="r">Fraud cases</th><th scope="col" className="r">Held now</th><th scope="col" className="r">Would hold</th><th /></>}
                {(g.id === "unmatched" || g.id === "release") && <><th scope="col">Complaint</th><th scope="col" className="r">Still in the account</th><th scope="col">Details that match</th><th /></>}
              </tr></thead>
              <tbody>
                {limited.map((p) => {
                  const done = decidedById[p.proposal_id];
                  if (done) return <DoneLine key={p.proposal_id} p={done} officer={officer} onDone={reload} cols={cols} />;
                  const isOpen = open === p.proposal_id;
                  const f = figures(p);
                  const [clear, why] = clearCase(p);
                  const changed = balanceChanged(p);
                  const btn = <button className="btn btn-s" type="button" aria-expanded={isOpen}
                    onClick={() => setOpenId(isOpen ? null : p.proposal_id)}>{isOpen ? "Close" : "Open"}</button>;
                  // a click anywhere on the row opens it, except on its own controls (checkbox, buttons, links)
                  const pick = (e) => { if (!e.target.closest("button, a, input, label, select")) setOpenId(p.proposal_id); };
                  return (
                    <tr key={p.proposal_id} className={rowClass(p.case_id, isOpen)} onClick={pick}>
                      {g.id === "narrow" && <>
                        <td>{bulk && !changed && clear && officer && !SNAPSHOT ? <span className="cbw"><input className="cb" type="checkbox" aria-label={`Select ${p.name}`} checked={picked.includes(p.proposal_id)} onChange={(e) => setPicked((x) => e.target.checked ? [...x, p.proposal_id] : x.filter((y) => y !== p.proposal_id))} /></span> : null}</td>
                        <td><b>{shortName(p.name)}</b></td>
                        <td className="r">{inrR(f.held)}</td><td className="r vic">{inrR(f.target)}</td><td className="r own" style={{ fontWeight: 700 }}>{inrR(f.release)}</td>
                        <td><ComplaintLink caseId={p.case_id} fallback={`${victim(p.victim_tag)}, ${clock(p.received_at)}`} />{changed && <><br /><span className="pill p-chg" style={{ marginTop: 6 }}>Updated</span></>}</td>
                        <td>{changed ? <><span className="pill p-look">Needs review</span><br /><span className="note">Balance changed {dayTime(p.proposed_at)}</span></>
                          : clear ? <span className="pill p-clear">Ready to approve</span> : <><span className="pill p-look">Needs review</span><br /><span className="note">{why}</span></>}</td>
                        <td className="r">{btn}</td></>}
                      {g.id === "freeze" && <>
                        <td><b>{shortName(p.name)}</b></td><td><ComplaintLink caseId={p.case_id} fallback={`${victim(p.victim_tag)}, ${clock(p.received_at)}`} /></td><td className="r">{Math.round(n(p.fraud_case_count) * 10) / 10 || 1}</td><td className="r">{inrR(f.held)}</td>
                        <td>{(() => { const t = attemptsOf(p, d.watch); return t.length ? <><b>{plural(t.length, "try", "tries")}</b>, {joinAnd(t.map((e) => clock(e.event_at)))}</> : "None"; })()}</td>
                        <td className="r">{btn}</td></>}
                      {g.id === "larger" && <>
                        <td><b>{shortName(p.name)}</b></td><td><ComplaintLink caseId={p.case_id} fallback={`${victim(p.victim_tag)}, ${clock(p.received_at)}`} /></td><td className="r">{Math.round(n(p.fraud_case_count) * 10) / 10 || 2}</td>
                        <td className="r">{inrR(f.held)}</td><td className="r" style={{ fontWeight: 700 }}>{inrR(f.target)}</td>
                        <td className="r">{btn}</td></>}
                      {(g.id === "unmatched" || g.id === "release") && <>
                        <td><ComplaintLink caseId={p.case_id} fallback={`${victim(p.victim_tag)}, ${clock(p.received_at)}`} /> · <b>{inrR(p.complaint_amount_inr)}</b><br /><span className="note">to {shortName(p.name)}</span></td>
                        <td className="r">{n(p.balance_today_inr) ? <b>{inrR(p.balance_today_inr)}</b> : <span className="note">₹0</span>}</td>
                        <td>{(() => { const m = matchCount(p); return <>{m.same} of {m.total}<br /><span className="note">{joinAnd(m.differ)} differ{m.differ.length === 1 ? "s" : ""}</span></>; })()}</td>
                        <td className="r">{btn}</td></>}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {openP && (
            <SidePanel title={title} itemKey={openP.proposal_id} onClose={() => setOpenId(null)} beside={listRef}>
              <DecisionCard key={openP.proposal_id} p={openP} officer={officer} onDone={decide} watch={d.watch} variant={g.id === "narrow" ? "inlist" : "panel"}
                position={`${{ narrow: "Smaller hold", freeze: "Freeze", larger: "Larger hold", unmatched: "Near match", release: "Release" }[g.id]} · ${rows.indexOf(openP) + 1} of ${rows.length}`} />
            </SidePanel>
          )}
          {shown.length > 10 && <MoreButton open={more} setOpen={setMore} label={`Show ${shown.length - 10} more`} style={{ marginTop: 10 }} />}
          <p className="note" style={{ marginTop: 8 }}>{g.id === "narrow" ? `Sorted by money to release, largest first, so the customers hurt most come up first. Ready to approve: the account is older than 1 year, little money runs through it (pass-through under 0.30), and the victim's share is under 5% of its balance.${bulk ? " These can be ticked and approved together." : ""} Other rows say why they need review.`
            : g.id === "larger" ? "Sorted by attempts to move held money, then by fraud cases. A larger hold covers the fraud-linked money of 90 days, never more than the balance, and never the whole account."
            : g.id === "freeze" ? "Sorted by attempts to move held money, then by number of complaints, then by amount held. An account that tried to empty itself during a hold is the strongest case for a freeze, so it comes first. No checkboxes: a full freeze needs reasons for each account."
              : "Sorted by money still in the account, because that is what can still be lost. A row with ₹0 left drops to the bottom. Its only remaining job is the police report."}</p>
          {sel.length > 0 && (
            <div className="selbar">
              <span><b>{sel.length} selected</b> · would release {inrR(sel.reduce((t, p) => t + figures(p).release, 0))} of customers' own money · victims' {inrR(sel.reduce((t, p) => t + figures(p).target, 0))} stays held</span>
              <span style={{ display: "flex", gap: 8 }}><button className="btn btn-q" type="button" onClick={() => setPicked([])}>Clear selection</button>
                <button className="btn" type="button" onClick={() => setSigning(true)}>Review and sign {sel.length}</button></span>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// several ready-to-approve smaller holds at once: each account still gets its own signed reason and audit entry
function SignStep({ list, officer, onBack, onSigned }) {
  const [texts, setTexts] = useState(() => Object.fromEntries(list.map((p) => [p.proposal_id, p.draft_text || ""])));
  const [editing, setEditing] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function sign() {
    setBusy(true);
    setError(null);
    try {
      for (const p of list) await api.approve(p.proposal_id, { officer, signed_text: texts[p.proposal_id] !== p.draft_text ? texts[p.proposal_id] : null });
      onSigned();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="card signstep">
      <h3 style={{ fontSize: 20 }}>Reduce {list.length} holds to the victim's share</h3>
      <p style={{ color: "#44505e" }}>Each account gets its own decision and audit entry, signed as {officer}. The reasons are written from each account's evidence. Edit any you disagree with.</p>
      {list.map((p) => (
        <div key={p.proposal_id} className="item">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}><b>{shortName(p.name)}</b><span>{inrR(p.current_hold_inr)} → <b>{inrR(p.amount_inr)}</b></span></div>
          {editing === p.proposal_id ? <textarea rows={4} value={texts[p.proposal_id]} onChange={(e) => setTexts((t) => ({ ...t, [p.proposal_id]: e.target.value }))} />
            : <p style={{ whiteSpace: "pre-wrap" }}>{rs(texts[p.proposal_id])}</p>}
          <button className="linkbtn" type="button" style={{ justifySelf: "start", textDecoration: "none", fontWeight: 600, color: "#44505e" }} onClick={() => setEditing(editing === p.proposal_id ? null : p.proposal_id)}>{editing === p.proposal_id ? "Done editing" : "Edit reason"}</button>
        </div>
      ))}
      <p className="note">Legal basis on all {list.length}: {rs(list[0]?.citation || "hold only the victim's amount (SOP 5(xii), 9.1(i)).")}</p>
      <div className="decided">Released money can be spent at once. If you change your mind later, a new hold can only cover what is still in the account.</div>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="acts"><button className="btn btn-p" type="button" disabled={busy} onClick={sign}>{busy ? "Signing…" : `Sign ${plural(list.length, "decision")} as ${officer}`}</button>
        <button className="btn btn-q" type="button" onClick={onBack}>Back to the list</button></div>
    </div>
  );
}

// ---------------------------------------------------------------- the right column
export function complaintText(t) {
  const v = victim(t.victim_tag);
  if (["FAILED", "CHECK_FAILED"].includes(t.run_status)) return rs(t.problem || "The system could not complete it.");
  if (t.run_status === "SENT_BACK") return `No payment with this UTR. Sent back to the police${t.sim_holds_at ? ` at ${clock(t.sim_holds_at)}` : ""}; nothing held yet.`;
  const parts = [];
  if (t.linked_at && n(t.held_inr)) parts.push(`${inrR(t.held_inr)} held. The watch had already caught it at ${clock(t.linked_at)}.`);
  else if (n(t.held_inr)) parts.push(`${inrR(t.held_inr)} of ${possessive(v)} money held on ${plural(n(t.held_accounts) || n(t.holds), "account")} by ${clock(t.sim_holds_at)}.`);
  const gone = [n(t.onward_inr) && `${inrR(t.onward_inr)} sent on to ${t.onward_bank || "another bank"}`, n(t.exits_inr) && `${inrR(t.exits_inr)} withdrawn as cash${t.exit_at ? ` at ${clock(t.exit_at)}` : ""}`].filter(Boolean);
  if (!n(t.held_inr) && gone.length) parts.push(`Money left before the complaint: ${joinAnd(gone)}.${n(t.onward_inr) ? ` ${t.onward_bank || "The other bank"} informed;` : ""}${t.marker_at ? " the account is marked to catch new money." : ""}`);
  else if (gone.length) parts.push(`${joinAnd(gone).replace(/^./, (c) => c.toUpperCase())}.`);
  return parts.join(" ") || "Nothing to hold here.";
}

function TodayList({ d, groups, onJump, pro }) {
  const [all, setAll] = useState(false);
  const FIRST = 5;
  // every decision waiting on a complaint, of any kind: proposals, overnight holds to confirm, holds whose clock
  // asks for a decision, and accounts to review; and the group to jump to for the first of them
  const extra = [["overnight", pro?.held_overnight || []], ["clock", pro?.queue || []], ["review", pro?.review || []]];
  const needs = (t) => d.waiting.filter((p) => p.case_id === t.case_id).length
    + extra.reduce((k, [, list]) => k + list.filter((x) => x.case_id === t.case_id).length, 0);
  const jumpFor = (t) => groups.find((g) => g.waiting.some((p) => p.case_id === t.case_id))?.id
    ?? extra.find(([, list]) => list.some((x) => x.case_id === t.case_id))?.[0];
  const failedRun = (t) => ["FAILED", "CHECK_FAILED"].includes(t.run_status);
  // only the complaints that are not Done (not completed, decisions waiting, or sent back), in the order they came in
  const isDone = (t) => !failedRun(t) && !needs(t) && t.run_status !== "SENT_BACK";
  const done = (d.done || []).filter((t) => !isDone(t)).sort((a, b) => String(a.received_at).localeCompare(String(b.received_at)));
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
        <h2 id="today-h" style={{ fontSize: 20 }}>Today's complaints <span style={{ color: "#5b6675", fontWeight: 400 }}>{done.length}</span></h2>
        <a href="#/queue" style={{ fontSize: 14 }}>All</a>
      </div>
      <ol className="card" style={{ listStyle: "none", padding: 0, margin: "0 0 20px" }}>
        {done.length === 0 && <li className="row-c"><span /><span className="note">{(d.done || []).length ? "Every complaint today is done." : "No complaints yet."}</span></li>}
        {(all ? done : done.slice(0, FIRST)).map((t) => {
          const mine = needs(t);
          const failed = failedRun(t);
          const jump = jumpFor(t);
          return (
            <li key={t.case_id} className="row-c">
              <span className="mono" style={{ fontSize: 16, fontWeight: 600 }}>{clock(t.received_at)}</span>
              <div>
                <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "4px 8px", alignItems: "center" }}>
                  <a href={`#/case/${encodeURIComponent(t.case_id)}`} style={{ fontWeight: 600 }}>{inrR(t.amount_inr)} · {victim(t.victim_tag)}</a>
                  {failed ? <a className="pill p-urgent" href="#g-failed" style={{ textDecoration: "none" }} onClick={(e) => { e.preventDefault(); onJump("failed", t.case_id); }}>Not completed</a>
                    : mine ? <a className="pill p-wait" href={`#g-${jump}`} style={{ textDecoration: "none" }} onClick={(e) => { e.preventDefault(); onJump(jump, t.case_id); }}>{mine} for you</a>
                      : <span className="pill p-done">{t.run_status === "SENT_BACK" ? "Sent back" : "Done"}</span>}
                </div>
                <p style={{ fontSize: 13, color: "#44505e", marginTop: 2 }}>{complaintText(t)}</p>
              </div>
            </li>
          );
        })}
        {done.length > FIRST && <li className="row-more"><MoreButton open={all} setOpen={setAll} label={`Show ${done.length - FIRST} more`} scrollTo="today-h" /></li>}
      </ol>
    </>
  );
}

function SinceHolds({ watch }) {
  const w = watch || [];
  if (!w.length) return null;
  const pick = (k) => w.filter((e) => k.includes(e.event_kind));
  const refused = pick(["CASHOUT_BLOCKED", "TRANSFER_BLOCKED"]);
  const caught = pick(["NEW_MONEY_HELD", "PROACTIVE_HOLD"]);
  const linked = new Set(pick(["LINKED_TO_VICTIM"]).map((e) => e.account_id));
  const watched = pick(["RECIPIENT_WATCHED"]);
  const honest = pick(["NEW_MONEY_HONEST"]);
  const flagged = pick(["PRE_ALERT_READY"]);
  const early = pick(["PRE_ALERT_NOT_NEEDED", "PRE_ALERT_ANSWERED"]);
  const sent = pick(["PRE_ALERT_SENT"]);
  const open = Math.max(0, flagged.length - pick(["PRE_ALERT_NOT_NEEDED"]).length - sent.length);
  const who = (e) => shortName(e.name || e.account_id);
  const sum = (xs) => inrR(xs.reduce((t, e) => t + n(e.amount_inr), 0));
  const names = (xs) => joinAnd([...new Set(xs.map(who))]);
  const largest = (xs) => xs.reduce((a, b) => (n(b.amount_inr) > n(a.amount_inr) ? b : a));
  const what = (e) => (e.event_kind === "CASHOUT_BLOCKED" ? `${possessive(who(e))} ${/crypto|wallet/i.test(e.counterparty || "") ? "crypto" : "ATM"} withdrawal of ${inrR(e.amount_inr)} at ${clock(e.event_at)}` : `${possessive(who(e))} ${inrR(e.amount_inr)} transfer at ${clock(e.event_at)}`);
  // up to two events are named; more than that is one counted line, so the card stays four short lines
  return (
    <>
      <h3 style={{ fontSize: 15, color: "#44505e", marginBottom: 8 }}>Since the holds</h3>
      <div className="card" style={{ padding: "14px 16px" }}>
        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 10, fontSize: 14 }}>
          {refused.length > 0 && <li><b>Refused.</b> {refused.length <= 2 ? `${joinAnd(refused.map(what))}.`
            : `${refused.length} attempts to move held money, ${sum(refused)} kept. The largest: ${what(largest(refused))}.`}</li>}
          {caught.length > 0 && <li><b>Caught.</b> {caught.length <= 2 ? `${joinAnd(caught.map((e) => `${inrR(e.amount_inr)} into ${who(e)} at ${clock(e.event_at)}`))}${caught.every((e) => linked.has(e.account_id)) ? `, ${caught.length === 1 ? "linked" : "each linked"} to a new complaint.` : "."}`
            : `${sum(caught)} of new money in ${caught.length} payments into ${names(caught)}${linked.size ? `; ${caught.filter((e) => linked.has(e.account_id)).length} linked to a new complaint` : ""}.`}</li>}
          {(flagged.length > 0 || sent.length > 0) && <li><b>Pre-alerts.</b> {plural(sent.length + flagged.length, "pre-alert")} after holds on first-time payers' money{sent.length ? `, ${sent.length} sent` : ""}.{early.length ? ` ${early.length === 1 ? "One payer" : `${early.length} payers`} complained later; the complaint linked to the money already held.` : ""}{open ? ` ${open === 1 ? "One waits" : `${open} wait`} for you.` : ""}</li>}
          {watched.length > 0 && <li><b>Watched.</b> {watched.length <= 2 ? `${joinAnd(watched.map((e) => `${who(e)}, the account ${shortName(e.counterparty || "a held account")} tried to pay at ${clock(e.event_at)}`))}.`
            : `${names(watched)}, after held accounts tried to pay them.`}</li>}
          {honest.length > 0 && <li><b>Not held.</b> {honest.length <= 2 ? `${joinAnd(honest.map((e) => `${inrR(e.amount_inr)} a customer paid ${who(e)} at ${clock(e.event_at)}`))}.`
            : `${honest.length} customer payments to ${names(honest)}, ${sum(honest)}, left free.`}</li>}
        </ul>
        <a href="#/audit" style={{ display: "inline-block", marginTop: 10, fontSize: 14 }}>All {plural(w.length, "event")} in the audit log</a>
      </div>
    </>
  );
}

// KYC packets: neutral when everything is filed; blue while a run is filing (design board "KYC packets card")
function Kyc({ d }) {
  const [all, setAll] = useState(false);
  const FIRST = 5;
  const done = (d.done || []).filter((t) => n(t.kyc));
  const filed = n(d.kyc_filed);
  const filing = n(d.queued) > 0;
  const sentBack = (d.done || []).filter((t) => t.run_status === "SENT_BACK");
  return (
    <>
      <div className="khead">
        <h3 id="kyc-h">KYC packets for the police</h3>
        {filing ? <span className="pill p-run">{d.queued} filing</span> : <span className="pill p-done">{filed} filed · 0 due</span>}
      </div>
      <section className="card" style={{ padding: "14px 16px" }} aria-labelledby="kyc-h">
        {filing && <p style={{ fontSize: 14, marginBottom: 10 }}><b>Filing now</b> for {plural(n(d.queued), "complaint")}. It goes in with the holds, usually within 3 minutes.</p>}
        {filed > 0 ? <p style={{ fontSize: 14, marginBottom: 10 }}>Every packet went to the portal in the same run as its holds. The rules allow 7 days, so <b>nothing is left to send.</b></p>
          : <p style={{ fontSize: 14 }}>No KYC packets were needed yet.</p>}
        {done.length > 0 && (
          <table className="t" style={{ fontSize: 14 }}>
            <thead><tr><th scope="col">Complaint</th><th scope="col" style={{ textAlign: "right" }}>Packets</th><th scope="col">Filed</th></tr></thead>
            <tbody>{(all ? done : done.slice(0, FIRST)).map((t) => <tr key={t.case_id}><td><span className="mono">{clock(t.received_at)}</span> {victim(t.victim_tag)}</td><td style={{ textAlign: "right" }}>{t.kyc}</td><td className="mono">{clock(t.kyc_filed_at || t.sim_holds_at)}</td></tr>)}</tbody>
          </table>
        )}
        {done.length > FIRST && <MoreButton open={all} setOpen={setAll} label={`Show ${done.length - FIRST} more`} style={{ marginTop: 10 }} />}
        {sentBack.length > 0 && <p className="note" style={{ marginTop: 8 }}>None for {joinAnd(sentBack.map((t) => `${possessive(victim(t.victim_tag))} ${clock(t.received_at)}`))} complaint{sentBack.length === 1 ? "" : "s"}: {sentBack.length === 1 ? "its notice was" : "their notices were"} sent back, so no account was named.</p>}
        <a href="#/audit" style={{ display: "inline-block", marginTop: 8, fontSize: 14 }}>Each packet in the audit log</a>
      </section>
    </>
  );
}
// ---------------------------------------------------------------- complaints the system could not complete
// First in the list: nothing was done automatically on these, so the officer has to. Same list and side panel as
// the other groups; "Work it again" is the same rerun the Complaints page offers.
function PinnedHere({ children }) {
  const footer = useContext(PanelFooter);
  return footer ? createPortal(children, footer) : children;
}
const failKey = (t) => `fail-${t.case_id}`;
const FAIL_WORDS = {
  CHECK_FAILED: "The checker's checks did not pass, so nothing was held automatically.",
  FAILED: "The system stopped before it finished, so nothing more was done automatically.",
};

function FailedRuns({ list, onDone, openId, setOpenId }) {
  const listRef = useRef(null);
  const rowClass = useRowClass();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  if (!list.length) return null;
  const openT = list.find((t) => failKey(t) === openId) || null;
  async function again(t) {
    setBusy(true);
    setNote(null);
    try {
      const r = await api.workOne(t.case_id, "pipeline");
      setNote(`Worked again in ${Number(r.seconds || 0).toFixed(1)} s.`);
      setOpenId(null);
      onDone?.();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div id="g-failed" style={{ marginTop: 12, scrollMarginTop: 110 }}>
      <section className="card" style={{ padding: "18px 20px", borderColor: "#e7b3ae" }} aria-labelledby="lg-failed">
        <div style={{ marginBottom: 12 }}>
          <h3 id="lg-failed" style={{ fontSize: 20 }}>Not completed · {list.length}</h3>
          <p style={{ color: "#7a211b", fontWeight: 600 }}>The system could not finish these complaints: nothing more was done automatically</p>
        </div>
        <div className="tw" ref={listRef}>
          <table className="t">
            <thead><tr><th scope="col">Complaint</th><th scope="col" className="r">Reported</th><th scope="col">What happened</th><th /></tr></thead>
            <tbody>
              {list.map((t) => {
                const isOpen = openId === failKey(t);
                const pick = (e) => { if (!e.target.closest("button, a, input, label, select")) setOpenId(failKey(t)); };
                return (
                  <tr key={t.case_id} className={rowClass(t.case_id, isOpen)} onClick={pick}>
                    <td><ComplaintLink caseId={t.case_id} fallback={`${victim(t.victim_tag)}, ${clock(t.received_at)}`} /></td>
                    <td className="r">{inrR(t.amount_inr)}</td>
                    <td>{t.run_status === "CHECK_FAILED" ? "The checks did not pass" : "Stopped before finishing"}</td>
                    <td className="r"><button className="btn btn-s" type="button" aria-expanded={isOpen}
                      onClick={() => setOpenId(isOpen ? null : failKey(t))}>{isOpen ? "Close" : "Open"}</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {openT && (
          <SidePanel title="Complaint not completed" itemKey={failKey(openT)} onClose={() => setOpenId(null)} beside={listRef}>
            <article className="card dc narrowcard">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
                <span className="kind">Not completed · {list.indexOf(openT) + 1} of {list.length}</span><span className="pill p-urgent">Needs you</span>
              </div>
              <h4 style={{ fontSize: 19, marginBottom: 6 }}>{inrR(openT.amount_inr)} reported at {clock(openT.received_at)} · {victim(openT.victim_tag)}</h4>
              <p style={{ fontSize: 14, marginBottom: 12 }}>{FAIL_WORDS[openT.run_status] || FAIL_WORDS.FAILED}</p>
              {openT.error_text && <>
                <p className="label" style={{ marginBottom: 4 }}>Where it stopped</p>
                <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: 12, padding: "10px 12px", background: "#f5f7f9", border: "1px solid #e8ecf0", borderRadius: 8, maxHeight: 220, overflow: "auto" }}>{String(openT.error_text).slice(0, 800)}</pre>
              </>}
              {note && <p role="status" style={{ fontSize: 14, marginTop: 8 }}>{note}</p>}
              <PinnedHere><div className="acts" style={{ flexWrap: "wrap", alignItems: "center" }}>
                <button className="btn btn-p" type="button" disabled={busy || !!SNAPSHOT} onClick={() => again(openT)}>{busy ? "Working…" : "Work it again"}</button>
                <a className="linkbtn" href={`#/case/${encodeURIComponent(openT.case_id)}`}>Open the complaint and its step log</a>
              </div></PinnedHere>
            </article>
          </SidePanel>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- any other waiting decision
// A safety net: a waiting proposal of a kind with no group above still appears, so every complaint marked "for you"
// has its decision in this list.
function OtherDecisions({ items, d, officer, onDone, openId, setOpenId }) {
  const listRef = useRef(null);
  const rowClass = useRowClass();
  if (!items.length) return null;
  const openP = items.find((p) => p.proposal_id === openId) || null;
  return (
    <div id="g-other" style={{ marginTop: 26, scrollMarginTop: 110 }}>
      <section className="card" style={{ padding: "18px 20px" }} aria-labelledby="lg-other">
        <h3 id="lg-other" style={{ fontSize: 20, marginBottom: 12 }}>Other decisions · {items.length}</h3>
        <div className="tw" ref={listRef}>
          <table className="t">
            <thead><tr><th scope="col">Account</th><th scope="col">Decision</th><th scope="col">Complaint</th><th /></tr></thead>
            <tbody>
              {items.map((p) => {
                const isOpen = openId === p.proposal_id;
                return (
                  <tr key={p.proposal_id} className={rowClass(p.case_id, isOpen)} onClick={(e) => { if (!e.target.closest("button, a")) setOpenId(p.proposal_id); }}>
                    <td><b>{shortName(p.name)}</b></td><td>{KIND[p.action_code] || p.action_code}</td><td><ComplaintLink caseId={p.case_id} fallback={`${victim(p.victim_tag)}, ${clock(p.received_at)}`} /></td>
                    <td className="r"><button className="btn btn-s" type="button" aria-expanded={isOpen} onClick={() => setOpenId(isOpen ? null : p.proposal_id)}>{isOpen ? "Close" : "Open"}</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {openP && (
          <SidePanel title="Decision" itemKey={openP.proposal_id} onClose={() => setOpenId(null)} beside={listRef}>
            <DecisionCard key={openP.proposal_id} p={openP} officer={officer} onDone={(p) => { setOpenId(null); onDone(p); }} watch={d.watch} variant="panel"
              position={`${KIND[openP.action_code] || "Decision"} · ${items.indexOf(openP) + 1} of ${items.length}`} />
          </SidePanel>
        )}
      </section>
    </div>
  );
}