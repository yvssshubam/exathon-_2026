import { useMemo, useState } from "react";
import { api, SNAPSHOT } from "../api.js";
import DecisionCard, { attemptsOf, balanceChanged, clearCase, figures, matchCount, Undo } from "../components/DecisionCard.jsx";
import { Failed, Loading } from "../components/bits.jsx";
import { ageWords, clock, duration, inrR, joinAnd, plural, rs, shortName, victim } from "../format.js";
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
    desc: "A freeze adds one thing to the hold: it blocks money that comes in later, until the police decide. Use it when an account keeps receiving fraud money." },
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
  const [justDone, setJustDone] = useState([]);       // decided this visit: rows stay in place as one grey line

  if (inbox.loading && !inbox.data) return <div className="wrap"><Loading what="Loading the morning" /></div>;
  if (inbox.error) return <div className="wrap"><Failed error={inbox.error} retry={inbox.reload} /></div>;
  const d = inbox.data;
  const decidedById = Object.fromEntries((d.decided || []).map((p) => [p.proposal_id, p]));
  const onDone = (p) => { setJustDone((j) => [...j, p]); inbox.reload(); day.reload(); onChange?.(); };
  const reload = () => { inbox.reload(); day.reload(); onChange?.(); };
  // waiting items, plus the ones decided this visit (kept where they were, from the snapshot taken before deciding)
  const items = [...d.waiting, ...justDone.filter((p) => !d.waiting.some((w) => w.proposal_id === p.proposal_id))];
  const groups = GROUPS.map((g) => ({ ...g, items: items.filter((p) => p.action_code === g.code), waiting: d.waiting.filter((p) => p.action_code === g.code) }));
  const decided = (d.decided || []).filter((p) => !justDone.some((j) => j.proposal_id === p.proposal_id));
  const total = d.waiting.length;

  return (
    <>
      <Band d={d} day={day.data} now={clockNow.data?.clock} groups={groups} total={total} />
      <main id="main" className="wrap" style={{ display: "flex", flexWrap: "wrap", gap: 28, alignItems: "flex-start", paddingTop: 24, paddingBottom: 40 }}>
        <section style={{ flex: "999 1 640px", minWidth: 0 }} aria-labelledby="dec-h">
          <div style={{ position: "sticky", top: 0, zIndex: 2, background: "#f3f5f7", padding: "4px 0 12px" }}>
            <h2 id="dec-h" style={{ fontSize: 20, marginBottom: 10 }}>Decisions for you <span style={{ color: "#5b6675", fontWeight: 400 }}>{total}</span></h2>
            <nav className="jump" aria-label="Jump to a group" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {groups.filter((g) => g.waiting.length).map((g) => {
                const clear = g.id === "narrow" && g.waiting.length >= 4 ? g.waiting.filter((p) => clearCase(p)[0] && !balanceChanged(p)).length : 0;
                return <a key={g.id} href={`#g-${g.id}`} onClick={(e) => { e.preventDefault(); document.getElementById(`g-${g.id}`)?.scrollIntoView({ behavior: "smooth" }); }}>
                  {g.jump} <b style={g.badge}>{g.waiting.length}{clear ? ` · ${clear} clear` : ""}</b></a>;
              })}
              {decided.length > 0 && <a href="#g-decided" onClick={(e) => { e.preventDefault(); document.getElementById("g-decided")?.scrollIntoView({ behavior: "smooth" }); }}>Decided today <b style={{ background: "#e8ecf0", color: "#44505e" }}>{decided.length}</b></a>}
            </nav>
          </div>
          {total === 0 && justDone.length === 0 && <p className="card" style={{ padding: "14px 18px", marginTop: 12 }}>Nothing is waiting for you.{d.done.length ? ` All ${plural(d.done.length, "complaint")} today are handled.` : " No complaints have come in yet."}</p>}
          {groups.filter((g) => g.items.length).map((g) => (
            <Group key={g.id} g={g} d={d} officer={officer} onDone={onDone} reload={reload} decidedById={decidedById} />
          ))}
          {decided.length > 0 && <DecidedToday list={decided} officer={officer} onDone={reload} />}
        </section>
        <aside style={{ flex: "1 1 360px", minWidth: 0 }} aria-labelledby="today-h">
          <TodayList d={d} groups={groups} />
          <SinceHolds watch={d.watch} />
          <Kyc d={d} />
        </aside>
      </main>
    </>
  );
}

// ---------------------------------------------------------------- the band
function Band({ d, day, now, groups, total }) {
  const done = d.done || [];
  const failed = done.filter((t) => ["FAILED", "CHECK_FAILED"].includes(t.run_status));
  const night = done.filter((t) => t.after_hours);
  const worked = done.filter((t) => t.sim_holds_at);
  const mins = worked.map((t) => minutesBetween(t.received_at, t.sim_holds_at)).filter((m) => m >= 0);
  const max = mins.length ? Math.max(...mins) : null;
  const ts = now || done.at(-1)?.received_at;
  const date = ts ? new Date(String(ts).slice(0, 10)) : null;
  const top = date ? `Review · ${DAYS[date.getDay()]} ${date.getDate()} ${MONTHS[date.getMonth()]} ${date.getFullYear()}${now ? ` · clock ${clock(now)}` : ""}` : "Review";
  let headline;
  if (!done.length) headline = "No complaints yet today.";
  else if (failed.length) headline = `${plural(done.length, "complaint")} today. ${failed.length} could not be completed by the system and ${failed.length === 1 ? "needs" : "need"} you.`;
  else headline = `${plural(done.length, "complaint")} today. ${done.length === 1 ? "It was" : "Every one was"} worked within ${max ?? 0} minute${max === 1 ? "" : "s"}${night.length ? `, ${night.length === done.length ? "all" : night.length} of them after office hours` : ""}.`;
  const onward = done.reduce((t, x) => t + n(x.onward), 0), exits = done.reduce((t, x) => t + n(x.exits), 0), kyc = n(d.kyc_filed);
  const filed = [onward && plural(onward, "onward entry", "onward entries"), exits && plural(exits, "cash withdrawal"), kyc && plural(kyc, "KYC packet")].filter(Boolean);
  const narrowWaiting = groups.find((g) => g.id === "narrow").waiting.length;
  const tool = day?.tool, today = day?.today;
  const pct = (a, b) => (n(b) ? Math.round((n(a) / n(b)) * 100) : 0);
  const nightHeld = night.filter((t) => t.sim_holds_at);
  const nightMins = today?.night_minutes || [];
  const frozen = (today?.frozen_names || []).map((x) => possessive(shortName(x)));
  const w = d.watch || [];
  const refused = w.filter((e) => ["CASHOUT_BLOCKED", "TRANSFER_BLOCKED"].includes(e.event_kind));
  const caught = w.filter((e) => e.event_kind === "NEW_MONEY_HELD");
  const linkedVictims = new Set(w.filter((e) => e.event_kind === "LINKED_TO_VICTIM").map((e) => e.case_id));
  const watched = w.filter((e) => e.event_kind === "RECIPIENT_WATCHED");
  return (
    <section style={{ background: "#1c2742", color: "#e4e9f5" }} aria-labelledby="band-h">
      <div className="wrap" style={{ paddingTop: 26, paddingBottom: 26 }}>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-start", gap: "12px 24px", marginBottom: 18 }}>
          <div style={{ maxWidth: 860 }}>
            <p style={{ fontSize: 13, letterSpacing: "0.05em", textTransform: "uppercase", color: "#a9b6d6", marginBottom: 6 }}>{top}</p>
            <h1 id="band-h" style={{ fontSize: 28, lineHeight: 1.25, color: "#fff", marginBottom: 8 }}>{headline}</h1>
            {done.length > 0 && <p style={{ color: "#c4cde3", fontSize: 15 }}>Money is held.{filed.length ? ` ${joinAnd(filed)} ${filed.length === 1 && (onward + exits + kyc) === 1 ? "is" : "are"} filed with the portal.` : ""} {total ? `Only ${plural(total, "decision")} ${total === 1 ? "needs" : "need"} you.` : "Nothing needs you."}</p>}
          </div>
          {d.morning_log && <a className="btn" href="#/audit" style={{ background: "transparent", color: "#fff", borderColor: "#5b6a93" }}>Handover note, {clock(d.morning_log.period_to)}</a>}
        </div>
        {done.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
            <Tile title="Victims' money secured" big={tool ? inrR(tool.victim_held_inr) : "…"} sub={day ? `${pct(tool.victim_held_inr, day.victim_paid_inr)}% of ${inrR(day.victim_paid_inr)} reported` : ""}
              manual={today && <>Manual process: <b style={{ color: "#fff" }}>{inrR(today.victim_held_inr)}</b> ({pct(today.victim_held_inr, day.victim_paid_inr)}%)</>} />
            <Tile title="Genuine customers' money blocked" big={tool ? inrR(tool.honest_locked_now_inr) : "…"} green
              sub={narrowWaiting && n(tool?.honest_locked_now_inr) ? `₹0 once you approve ${plural(narrowWaiting, "smaller hold")}` : n(tool?.honest_locked_now_inr) ? "" : "None of their own money is held"}
              manual={today && <>Manual process: <b style={{ color: "#fff" }}>{inrR(today.honest_locked_inr)}</b>{frozen.length ? `, ${joinAnd(frozen)} full account${frozen.length === 1 ? "" : "s"} frozen` : ""}</>} />
            <Tile title="First hold, after-hours complaints" big={nightHeld.length ? duration(Math.max(...nightHeld.map((t) => minutesBetween(t.received_at, t.sim_holds_at)))) : "–"}
              sub={nightHeld.length ? `at ${joinAnd(nightHeld.map((t) => clock(t.received_at)))}` : "No after-hours complaint needed a hold"}
              manual={nightMins.length > 0 && <>Manual process: <b style={{ color: "#fff" }}>{Math.min(...nightMins) === Math.max(...nightMins) ? duration(Math.min(...nightMins)) : `${duration(Math.min(...nightMins))} to ${duration(Math.max(...nightMins))}`}</b></>} />
            <div style={{ background: "#fbf0dc", borderRadius: 10, padding: "16px 18px", color: "#3d2600" }}>
              <p style={{ fontSize: 14, color: "#6b4200" }}>Decisions for you</p>
              <p style={{ fontSize: 32, fontWeight: 700, lineHeight: 1.2, marginBottom: 6 }}>{total}</p>
              {groups.filter((g) => g.waiting.length).map((g) => (
                <a key={g.id} className="tile-link" href={`#g-${g.id}`} onClick={(e) => { e.preventDefault(); document.getElementById(`g-${g.id}`)?.scrollIntoView({ behavior: "smooth" }); }}><span>{g.tile}</span><b>{g.waiting.length}</b></a>
              ))}
              {failed.length > 0 && <a className="tile-link" href={`#/case/${encodeURIComponent(failed[0].case_id)}`}><span>Complaint the system could not complete</span><b>{failed.length}</b></a>}
            </div>
          </div>
        )}
        {w.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: "6px 14px", marginTop: 14, fontSize: 14, color: "#c4cde3" }}>
            <b style={{ color: "#fff" }}>Since the holds:</b>
            {[refused.length && `${plural(refused.length, "cash-out")} refused, ${inrR(refused.reduce((t, e) => t + n(e.amount_inr), 0))} kept`,
              caught.length && `${inrR(caught.reduce((t, e) => t + n(e.amount_inr), 0))} of new money caught${linkedVictims.size ? `, linked to ${plural(linkedVictims.size, "victim")}` : ""}`,
              watched.length && `${plural(watched.length, "new account")} watched`].filter(Boolean).flatMap((x, i) => (i ? [<span key={`s${i}`} aria-hidden="true">·</span>, <span key={i}>{x}</span>] : [<span key={i}>{x}</span>]))}
            <a href="#/audit" style={{ color: "#fff" }}>All {plural(w.length, "event")}</a>
          </div>
        )}
        {done.length > 0 && <p style={{ marginTop: 10, fontSize: 12, color: "#a9b6d6" }}>"Manual process" replays today's practice on the same transactions: the desk opens at 10:00 and closes at 18:00, takes about 20 minutes for the first account and 15 for each next one, and freezes whole accounts.{done.some((t) => t.run_status === "SENT_BACK") ? ` The ${joinAnd(done.filter((t) => t.run_status === "SENT_BACK").map((t) => clock(t.received_at)))} complaint is left out of both: nothing matched it.` : ""}</p>}
      </div>
    </section>
  );
}

function Tile({ title, big, sub, manual, green }) {
  return (
    <div style={{ background: "#26335a", borderRadius: 10, padding: "16px 18px" }}>
      <p style={{ fontSize: 14, color: "#c4cde3" }}>{title}</p>
      <p style={{ fontSize: 32, fontWeight: 700, color: "#fff", lineHeight: 1.2 }}>{big}</p>
      {sub && <p style={{ fontSize: 14, color: green ? "#b9dcc9" : "#c4cde3", marginTop: 4 }}>{sub}</p>}
      {manual && <p style={{ fontSize: 13, color: "#a9b6d6", marginTop: 8, paddingTop: 8, borderTop: "1px solid #34437a" }}>{manual}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- a decision group: cards up to 3, a list from 4
function Group({ g, d, officer, onDone, reload, decidedById }) {
  const list = g.items.length >= 4;
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
      {list && <ListGroup g={g} d={d} officer={officer} onDone={onDone} reload={reload} decidedById={decidedById} />}
    </div>
  );
}

// one grey line for a row decided this visit: who decided and when, and the undo for that kind
function DoneLine({ p, officer, onDone, card, cols }) {
  const outcome = p.decision_outcome || p.proposal_status;
  const word = outcome === "REJECTED" ? (p.action_code === "UNMATCHED_NOTICE" ? "Left with the police" : `Kept at ${inrR(p.current_hold_inr || p.amount_inr)}`)
    : { PROPOSE_NARROW: `Reduced to ${inrR(p.decided_amount_inr ?? p.amount_inr)}`, PROPOSE_FULL_FREEZE: "Frozen", UNMATCHED_NOTICE: `Accepted, ${inrR(p.amount_inr)} held`, PROPOSE_RELEASE: "Released" }[p.action_code];
  const body = <><span style={{ color: "#5b6675" }}>{word} by {p.decided_by}, {clock(p.decided_at)}</span><Undo p={p} officer={officer} onDone={onDone} compact /></>;
  if (cols) return <tr className="done"><td colSpan={cols - 1}><b style={{ color: "#5b6675" }}>{shortName(p.name)}</b> <span style={{ marginLeft: 16 }}>{word} by {p.decided_by}, {clock(p.decided_at)}</span></td><td className="r"><Undo p={p} officer={officer} onDone={onDone} compact /></td></tr>;
  return <div className={card ? "card" : ""} style={{ padding: "14px 18px", marginBottom: 14, display: "flex", flexWrap: "wrap", gap: "8px 16px", alignItems: "center", background: "#fafbfc" }}>
    <b style={{ color: "#5b6675" }}>{shortName(p.name)}</b>{body}</div>;
}

function ListGroup({ g, d, officer, onDone, reload, decidedById }) {
  const [filter, setFilter] = useState("all");
  const [complaint, setComplaint] = useState("all");
  const [openId, setOpenId] = useState(null);
  const [more, setMore] = useState(false);
  const [picked, setPicked] = useState([]);
  const [signing, setSigning] = useState(false);
  const rows = useMemo(() => {
    let r = [...g.items];
    if (g.id === "narrow") r.sort((a, b) => figures(b).release - figures(a).release);
    if (g.id === "freeze") r.sort((a, b) => attemptsOf(b, d.watch).length - attemptsOf(a, d.watch).length || n(b.complaint_links) - n(a.complaint_links) || n(b.current_hold_inr) - n(a.current_hold_inr));
    if (g.id === "unmatched") r.sort((a, b) => n(b.balance_today_inr) - n(a.balance_today_inr));
    return r;
  }, [g.items, g.id, d.watch]);
  const isClear = (p) => clearCase(p)[0] && !balanceChanged(p);
  let shown = rows.filter((p) => complaint === "all" || p.case_id === complaint);
  if (g.id === "narrow" && filter !== "all") shown = shown.filter((p) => (filter === "clear") === isClear(p));
  const limited = more ? shown : shown.slice(0, 10);
  const complaints = [...new Map(rows.map((p) => [p.case_id, p.label])).entries()];
  const firstLook = rows.find((p) => !decidedById[p.proposal_id] && !isClear(p));
  const open = openId ?? firstLook?.proposal_id ?? null;
  const waitingRows = rows.filter((p) => !decidedById[p.proposal_id]);
  const own = waitingRows.reduce((t, p) => t + figures(p).release, 0);
  const sel = picked.map((id) => rows.find((p) => p.proposal_id === id)).filter((p) => p && !decidedById[p.proposal_id] && isClear(p));
  const nextAfter = (p) => rows.find((x) => x.proposal_id !== p.proposal_id && !decidedById[x.proposal_id] && !isClear(x))?.proposal_id ?? null;
  const decide = (p) => { setOpenId(nextAfter(p)); onDone(p); };

  const title = { narrow: "Reduce a genuine customer's hold", freeze: "Freeze the whole account?", unmatched: "Notice does not fully match", release: "Release a hold" }[g.id];
  const cols = g.id === "narrow" ? 8 : g.id === "freeze" ? 5 : 4;
  return (
    <section className="card" style={{ padding: "18px 20px", ...(g.id === "unmatched" ? { borderColor: "#e7b3ae" } : {}) }} aria-labelledby={`lg-${g.id}`}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: "10px 16px", marginBottom: 12 }}>
        <div>
          <h3 id={`lg-${g.id}`} style={{ fontSize: 20 }}>{title} · {waitingRows.length}</h3>
          {g.id === "narrow" && <p className="note" style={{ fontSize: 14 }}>{inrR(own)} of their own money is blocked. The victims' share stays held whatever you decide.</p>}
          {g.id === "unmatched" && <p style={{ color: "#7a211b", fontWeight: 600 }}>Nothing is held on any of these yet</p>}
        </div>
        {g.id === "narrow" && (
          <div className="chips">
            {[["all", "All", rows.length], ["clear", "Clear cases", rows.filter(isClear).length], ["look", "Need a look", rows.filter((p) => !isClear(p)).length]].map(([k, l, c]) => (
              <button key={k} className={`chip${filter === k ? " on" : ""}`} type="button" onClick={() => setFilter(k)}>{l} <span>{c}</span></button>
            ))}
          </div>
        )}
        {rows.length > 10 && (
          <label className="note" style={{ display: "flex", alignItems: "center", gap: 8 }}>Complaint
            <select value={complaint} onChange={(e) => setComplaint(e.target.value)}><option value="all">All</option>{complaints.map(([id, l]) => <option key={id} value={id}>{l}</option>)}</select></label>
        )}
      </div>
      {signing ? <SignStep list={sel} officer={officer} onBack={() => setSigning(false)} onSigned={() => { setSigning(false); setPicked([]); reload(); }} /> : (
        <>
          <div className="tw">
            <table className="t">
              <thead><tr>
                {g.id === "narrow" && <><th scope="col"><span className="sr">Select</span></th><th scope="col">Customer</th><th scope="col" className="r">Held</th><th scope="col" className="r">Victim's share</th><th scope="col" className="r">Would release</th><th scope="col">Complaint</th><th scope="col">Case</th><th /></>}
                {g.id === "freeze" && <><th scope="col">Account</th><th scope="col" className="r">Complaints</th><th scope="col" className="r">Held</th><th scope="col">Tried to move held money</th><th /></>}
                {(g.id === "unmatched" || g.id === "release") && <><th scope="col">Complaint</th><th scope="col" className="r">Still in the account</th><th scope="col">Details that match</th><th /></>}
              </tr></thead>
              <tbody>
                {limited.map((p, i) => {
                  const done = decidedById[p.proposal_id];
                  if (done) return <DoneLine key={p.proposal_id} p={done} officer={officer} onDone={reload} cols={cols} />;
                  const isOpen = open === p.proposal_id;
                  const f = figures(p);
                  const [clear, why] = clearCase(p);
                  const changed = balanceChanged(p);
                  const btn = <button className="btn btn-s" type="button" onClick={() => setOpenId(isOpen ? -1 : p.proposal_id)}>{isOpen ? "Close" : "Open"}</button>;
                  return [
                    <tr key={p.proposal_id} className={isOpen ? "open" : ""}>
                      {g.id === "narrow" && <>
                        <td>{changed ? <span className="pill p-chg">New</span> : clear && officer && !SNAPSHOT ? <span className="cbw"><input className="cb" type="checkbox" aria-label={`Select ${p.name}`} checked={picked.includes(p.proposal_id)} onChange={(e) => setPicked((x) => e.target.checked ? [...x, p.proposal_id] : x.filter((y) => y !== p.proposal_id))} /></span> : null}</td>
                        <td><b>{shortName(p.name)}</b><br /><span className="note">{p.account_type}, {ageWords(p.opened_on, p.held_since).replace(/ \d+ months?$/, "")}</span></td>
                        <td className="r">{inrR(f.held)}</td><td className="r vic">{inrR(f.target)}</td><td className="r own" style={{ fontWeight: 700 }}>{inrR(f.release)}</td>
                        <td className="mono">{clock(p.received_at)}</td>
                        <td>{changed ? <><span className="pill p-chg">Changed at {clock(p.proposed_at)}</span><br /><span className="note">Balance moved. Open to review again.</span></>
                          : clear ? <span className="pill p-clear">Clear case</span> : <><span className="pill p-look">Need a look</span><br /><span className="note">{why}</span></>}</td>
                        <td className="r">{btn}</td></>}
                      {g.id === "freeze" && <>
                        <td><b>{shortName(p.name)}</b></td><td className="r">{Math.round(n(p.complaint_links)) || 1}</td><td className="r">{inrR(f.held)}</td>
                        <td>{(() => { const t = attemptsOf(p, d.watch); return t.length ? <><b>{plural(t.length, "try", "tries")}</b>, {joinAnd(t.map((e) => clock(e.event_at)))}</> : "None"; })()}</td>
                        <td className="r">{btn}</td></>}
                      {(g.id === "unmatched" || g.id === "release") && <>
                        <td><b>{clock(p.received_at)} · {inrR(p.complaint_amount_inr)}</b><br /><span className="note">{victim(p.victim_tag)}, to {shortName(p.name)}</span></td>
                        <td className="r">{n(p.balance_today_inr) ? <b>{inrR(p.balance_today_inr)}</b> : <span className="note">₹0</span>}</td>
                        <td>{(() => { const m = matchCount(p); return <>{m.same} of {m.total}<br /><span className="note">{joinAnd(m.differ)} differ{m.differ.length === 1 ? "s" : ""}</span></>; })()}</td>
                        <td className="r">{btn}</td></>}
                    </tr>,
                    isOpen && <tr key={`${p.proposal_id}-o`} className="open"><td colSpan={cols} style={{ padding: "0 14px 14px" }}>
                      <DecisionCard p={p} officer={officer} onDone={decide} watch={d.watch} variant={g.id === "narrow" ? "inlist" : "panel"}
                        position={`${{ narrow: "Smaller hold", freeze: "Freeze", unmatched: "Near match", release: "Release" }[g.id]} · ${i + 1} of ${rows.length}`} />
                    </td></tr>,
                  ];
                })}
              </tbody>
            </table>
          </div>
          {shown.length > 10 && !more && <button className="btn" type="button" style={{ marginTop: 10 }} onClick={() => setMore(true)}>Show {shown.length - 10} more</button>}
          <p className="note" style={{ marginTop: 8 }}>{g.id === "narrow" ? "Sorted by money to release, largest first, so the customers hurt most come up first. A clear case is an account older than 1 year, with no cash out in 90 days, where the victim's share is under 5% of the balance. Other rows say why they need a look."
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

// several clear-case smaller holds at once: each account still gets its own signed reason and audit entry
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

// ---------------------------------------------------------------- decided today
function DecidedToday({ list, officer, onDone }) {
  const [show, setShow] = useState(list.length <= 3);
  return (
    <div id="g-decided" style={{ marginTop: 26, scrollMarginTop: 110 }}>
      <h3 style={{ fontSize: 17, marginBottom: 10 }}>Decided today <span style={{ color: "#5b6675", fontWeight: 400 }}>{list.length}</span>
        {!show && <button className="linkbtn" type="button" style={{ marginLeft: 12, fontSize: 14 }} onClick={() => setShow(true)}>Show</button>}</h3>
      {show && list.map((p) => {
        const rej = p.decision_outcome === "REJECTED";
        const word = p.undo_code ? { HOLD_AGAIN: "Held again", UNFREEZE: "Unfrozen", RELEASE: "Released" }[p.undo_code]
          : rej ? "Kept" : { PROPOSE_NARROW: "Reduced", PROPOSE_FULL_FREEZE: "Frozen", UNMATCHED_NOTICE: "Accepted", PROPOSE_RELEASE: "Released" }[p.action_code];
        const what = { PROPOSE_NARROW: rej ? `hold kept at ${inrR(p.current_hold_inr || p.amount_inr)}` : `hold reduced to ${inrR(p.decided_amount_inr ?? p.amount_inr)}`,
          PROPOSE_FULL_FREEZE: rej ? "hold kept, not frozen" : `full account (${inrR(p.decided_amount_inr ?? p.amount_inr)})`,
          UNMATCHED_NOTICE: rej ? "left with the police" : `near match accepted, ${inrR(p.amount_inr)}`, PROPOSE_RELEASE: "hold released" }[p.action_code];
        return (
          <div key={p.proposal_id} className="card" style={{ padding: "14px 18px", display: "flex", flexWrap: "wrap", gap: "8px 16px", alignItems: "baseline", marginBottom: 8 }}>
            <span className="pill p-done">{word}</span>
            <span><b>{shortName(p.name)}</b>, {what}, by {p.decided_by} at {clock(p.decided_at)}{p.decided_reason ? `: "${rs(p.decided_reason)}"` : "."}</span>
            {p.action_code === "PROPOSE_FULL_FREEZE" && !rej && !n(p.victim_amount_inr) && <span className="note">Not counted as {victim(p.victim_tag)}'s money: {shortName(p.name)} received none of it.</span>}
            {p.undo_code && <span className="note">{p.undone_by} at {clock(p.undone_at)}: "{p.undo_reason}"</span>}
            <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}><Undo p={p} officer={officer} onDone={onDone} compact /><a href={`#/record/${encodeURIComponent(p.account_id)}`}>Hold record</a></span>
          </div>
        );
      })}
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

function TodayList({ d, groups }) {
  const done = d.done || [];
  const jumpFor = (t) => groups.find((g) => g.waiting.some((p) => p.case_id === t.case_id))?.id;
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10 }}>
        <h2 id="today-h" style={{ fontSize: 20 }}>Today's complaints <span style={{ color: "#5b6675", fontWeight: 400 }}>{done.length}</span></h2>
        <a href="#/queue" style={{ fontSize: 14 }}>All</a>
      </div>
      <ol className="card" style={{ listStyle: "none", padding: 0, margin: "0 0 20px" }}>
        {done.length === 0 && <li className="row-c"><span /><span className="note">No complaints yet.</span></li>}
        {done.map((t) => {
          const mine = d.waiting.filter((p) => p.case_id === t.case_id).length;
          const failed = ["FAILED", "CHECK_FAILED"].includes(t.run_status);
          const jump = jumpFor(t);
          return (
            <li key={t.case_id} className="row-c">
              <span className="mono" style={{ fontSize: 16, fontWeight: 600 }}>{clock(t.received_at)}</span>
              <div>
                <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "4px 8px", alignItems: "center" }}>
                  <a href={`#/case/${encodeURIComponent(t.case_id)}`} style={{ fontWeight: 600 }}>{inrR(t.amount_inr)} · {victim(t.victim_tag)}</a>
                  {failed ? <a className="pill p-urgent" href={`#/case/${encodeURIComponent(t.case_id)}`} style={{ textDecoration: "none" }}>Not completed</a>
                    : mine ? <a className="pill p-wait" href={`#g-${jump}`} style={{ textDecoration: "none" }} onClick={(e) => { e.preventDefault(); document.getElementById(`g-${jump}`)?.scrollIntoView({ behavior: "smooth" }); }}>{mine} for you</a>
                      : <span className="pill p-done">{t.run_status === "SENT_BACK" ? "Sent back" : "Done"}</span>}
                </div>
                <p style={{ fontSize: 13, color: "#44505e", marginTop: 2 }}>{complaintText(t)}</p>
                {t.after_hours && <p className="note" style={{ marginTop: 2 }}>After office hours</p>}
              </div>
            </li>
          );
        })}
      </ol>
    </>
  );
}

function SinceHolds({ watch }) {
  const w = watch || [];
  if (!w.length) return null;
  const pick = (k) => w.filter((e) => k.includes(e.event_kind));
  const refused = pick(["CASHOUT_BLOCKED", "TRANSFER_BLOCKED"]);
  const caught = pick(["NEW_MONEY_HELD"]);
  const linked = new Set(pick(["LINKED_TO_VICTIM"]).map((e) => e.account_id));
  const watched = pick(["RECIPIENT_WATCHED"]);
  const honest = pick(["NEW_MONEY_HONEST"]);
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
            <tbody>{done.map((t) => <tr key={t.case_id}><td><span className="mono">{clock(t.received_at)}</span> {victim(t.victim_tag)}</td><td style={{ textAlign: "right" }}>{t.kyc}</td><td className="mono">{clock(t.kyc_filed_at || t.sim_holds_at)}</td></tr>)}</tbody>
          </table>
        )}
        {sentBack.length > 0 && <p className="note" style={{ marginTop: 8 }}>None for {joinAnd(sentBack.map((t) => `${possessive(victim(t.victim_tag))} ${clock(t.received_at)}`))} complaint{sentBack.length === 1 ? "" : "s"}: {sentBack.length === 1 ? "its notice was" : "their notices were"} sent back, so no account was named.</p>}
        <a href="#/audit" style={{ display: "inline-block", marginTop: 8, fontSize: 14 }}>Each packet in the audit log</a>
      </section>
    </>
  );
}