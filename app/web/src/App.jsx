import { useState } from "react";
import { api, SNAPSHOT } from "./api.js";
import { Logo } from "./components/bits.jsx";
import { clock, plural } from "./format.js";
import { setShowManual, useShowManual } from "./demo.js";
import { useLoad, useOfficer, useRoute } from "./hooks.js";
import Case from "./pages/Case.jsx";
import Compare from "./pages/Compare.jsx";
import { Audit, Holds } from "./pages/Ledger.jsx";
import Narrator from "./pages/Narrator.jsx";
import Queue from "./pages/Queue.jsx";
import Record from "./pages/Record.jsx";
import Review from "./pages/Review.jsx";

// The officer's app has four tabs. The demo views (manual vs system, the whole night) and the replay controls live
// in the dark Demo mode bar above it: judges see that bar, officers never do.
const TABS = [["review", "Review"], ["queue", "Complaints"], ["holds", "Holds"], ["audit", "Audit log"]];
const MORNING = "2026-10-18 09:00:00";
const DAY_END = "2026-10-18 23:59:00";
const CAPTION = {
  compare: "Built to show judges the difference. Officers never see this page.",
  narrator: "Shows three banks at once, which no single bank's officer can see.",
};

const LATER = "2026-12-18 00:00:00";      // day 61 of the overnight holds: the draft's clocks run out

function DemoBar({ page, onRan }) {
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const cfg = useLoad(() => api.config().catch(() => null), [note]);
  const mode = cfg.data?.proactive_hold_mode || "rbi_draft";
  const other = mode === "rbi_draft" ? "current_sop" : "rbi_draft";
  const MODE = { rbi_draft: "Draft mode (RBI draft)", current_sop: "Current SOP mode" };
  const view = page === "compare" ? "compare" : page === "narrator" ? "narrator" : "console";
  const manual = useShowManual();
  async function run(key, fn, say) {
    setBusy(key);
    setNote(null);
    try {
      setNote(say(await fn()));
      onRan();
    } catch (e) {
      setNote(e.message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="demobar" role="region" aria-label="Demo controls">
      <div className="wrap" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8, paddingTop: 8, paddingBottom: 8 }}>
        {/* one bucket for the demo simulation: the demo text switch and every control that runs the simulated night */}
        <div className="db-bucket" role="group" aria-label="Demo simulation">
          <button className="db-label db-toggle" type="button" aria-pressed={manual} onClick={() => setShowManual(!manual)}
            title="Show or hide the demo comparison with today's manual process">
            <span className="db-dot" aria-hidden="true" />Demo mode
            <span className={`db-switch${manual ? " on" : ""}`}>{manual ? "Manual process shown" : "Show manual process"}</span>
          </button>
          {page === "review" || page === "" ? (
            <span style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
              <span className="db-note" title="Proactive holds: the RBI draft's temporary debit hold, or only the room under the existing notice (today's SOP)">{MODE[mode]}</span>
              <button className="db-act" type="button" disabled={!!busy} onClick={() => run("mode", () => api.night(MORNING, true, "pipeline", other),
                (r) => `Switched to ${MODE[other].toLowerCase()} and reran the night to ${clock(r.until)} (${plural(r.events, "event")}, ${r.seconds} s).`)}>
                {busy === "mode" ? "Rerunning…" : `Switch to ${MODE[other].replace(" mode", "").replace(" (RBI draft)", "")}`}</button>
              <button className="db-act" type="button" disabled={!!busy} onClick={() => run("night", () => api.night(MORNING, true, "pipeline", mode),
                (r) => `The night ran from ${clock(r.from)} to ${clock(r.until)}: ${plural(r.events, "event")} across the three banks, in ${r.seconds} s.`)}>
                {busy === "night" ? "Running the night…" : "Replay the night to 09:00"}</button>
              <button className="db-act" type="button" disabled={!!busy} onClick={() => run("day", () => api.night(DAY_END, false),
                (r) => `Ran on to ${clock(r.until)}: every complaint of 18 Oct arrived and was handled (${plural(r.events, "event")}, ${r.seconds} s).`)}>
                {busy === "day" ? "Running the rest of the day…" : "Run the rest of the day"}</button>
              <button className="db-act" type="button" disabled={!!busy} onClick={() => run("later", () => api.night(LATER, false),
                (r) => `Jumped ahead to ${String(r.until).slice(0, 10)}: the overnight holds' clocks ran (${plural(r.events, "event")}, ${r.seconds} s).`)}>
                {busy === "later" ? "Jumping ahead…" : "Jump ahead 60 days"}</button>
              <button className="db-act" type="button" disabled={!!busy} onClick={() => run("reset", () => api.reset(), () => "Reset: every complaint waits on the portal again.")}>
                {busy === "reset" ? "Resetting…" : "Reset"}</button>
            </span>
          ) : <span>{CAPTION[view] || "Judges see this bar. Officers never do."}</span>}
        </div>
        <nav className="seg" aria-label="Demo views" style={{ alignSelf: "center" }}>
          <a className={view === "console" ? "on" : ""} href="#/review" aria-current={view === "console" ? "page" : undefined}>Officer console</a>
          <a className={view === "compare" ? "on" : ""} href="#/compare" aria-current={view === "compare" ? "page" : undefined}>Manual vs system</a>
          <a className={view === "narrator" ? "on" : ""} href="#/narrator" aria-current={view === "narrator" ? "page" : undefined}>The whole night</a>
        </nav>
      </div>
      {note && <div className="wrap" role="status" style={{ paddingBottom: 8, color: "#e4e9f0", textAlign: "center" }}>{note}</div>}
    </div>
  );
}

function SignIn({ onSignIn }) {
  const [name, setName] = useState("");
  return (
    <main id="main" className="wrap">
      <form className="card signin" onSubmit={(e) => { e.preventDefault(); if (name.trim()) onSignIn(name.trim()); }}>
        <h1 style={{ fontSize: 24 }}>Sign in</h1>
        <p className="note">Bank X · nodal desk. Every decision is signed with this name and goes into the audit log and the hold record.</p>
        <label>Your name<input value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required autoFocus /></label>
        <button className="btn btn-p" type="submit">Sign in as nodal officer</button>
        <a href="#/review" className="note">Look without signing in (read only)</a>
      </form>
    </main>
  );
}

export default function App() {
  const route = useRoute();
  const [officer, setOfficer] = useOfficer();
  const [version, setVersion] = useState(0);         // a demo run or reset: every page loads again
  const [tick, setTick] = useState(0);               // a decision: only the waiting count reloads
  const inbox = useLoad(() => api.inbox(), [version, tick]);
  const pro = useLoad(() => api.proactive().catch(() => null), [version, tick]);
  const [page, a, b] = route;
  // the same count as Review's "Decisions for you": proposals, plus proactive holds held overnight and due
  const waiting = inbox.data ? (inbox.data.waiting?.length || 0) + (pro.data?.held_overnight?.length || 0)
    + (inbox.data.done || []).filter((t) => ["FAILED", "CHECK_FAILED"].includes(t.run_status)).length
    + (pro.data?.queue?.length || 0) + (pro.data?.review?.length || 0) : null;
  const tab = page === "case" ? "queue" : page === "record" ? "holds" : page;
  const bump = () => setTick((v) => v + 1);
  const ran = () => setVersion((v) => v + 1);

  let body;
  if (page === "signin") body = <SignIn onSignIn={(n) => { setOfficer(n); window.location.hash = "#/review"; }} />;
  else if (page === "case" && a) body = <Case key={`${a}-${version}`} caseId={a} accountId={b} officer={officer} onChange={bump} />;
  else if (page === "record" && a) body = <Record key={`${a}-${version}`} accountId={a} officer={officer} onChange={bump} />;
  else if (page === "compare") body = <Compare key={`${a || ""}-${version}`} caseId={a} />;
  else if (page === "queue") body = <Queue key={version} onChange={bump} />;
  else if (page === "holds") body = <Holds key={version} />;
  else if (page === "audit") body = <Audit key={`${a || ""}-${version}`} caseId={a} officer={officer} onChange={bump} />;
  else if (page === "narrator") body = <Narrator key={version} victim={a} />;
  else body = <Review key={version} officer={officer} onChange={bump} />;

  return (
    <div className="ph">
      <a className="skip" href="#main">Skip to content</a>
      {!SNAPSHOT && <DemoBar page={page || "review"} onRan={ran} />}
      {SNAPSHOT && <p className="snapshotbar" role="status">{SNAPSHOT.note || `Saved copy of the console, ${SNAPSHOT.saved_at}. You can look at everything; nothing can be changed here.`}</p>}
      <header style={{ background: "#fff", borderBottom: "1px solid #d9dfe5" }}>
        <div className="wrap" style={{ display: "flex", flexWrap: "wrap", alignItems: "stretch", gap: "0 36px" }}>
          <a href="#/review" style={{ display: "flex", alignItems: "center", gap: 10, textDecoration: "none", color: "#17212b", padding: "10px 0" }}>
            <Logo />
            <span style={{ lineHeight: 1.2 }}><b style={{ display: "block" }}>Proportionate Holds</b><span style={{ fontSize: 13, color: "#5b6675" }}>Bank X · nodal desk</span></span>
          </a>
          <nav className="topnav" aria-label="Main">
            {TABS.map(([href, label]) => (
              <a key={href} className={tab === href || (href === "review" && !page) ? "on" : ""} href={`#/${href}`} aria-current={tab === href ? "page" : undefined}>
                {label}
                {href === "review" && waiting > 0 && <span className="count">{waiting}<span className="sr"> decisions waiting</span></span>}
              </a>
            ))}
          </nav>
          {officer ? (
            <div className="who">
              <span className="avatar" aria-hidden="true">{officer.trim()[0].toUpperCase()}</span>
              <span style={{ lineHeight: 1.2 }}><b style={{ display: "block" }}>{officer}</b>
                <span style={{ fontSize: 13, color: "#5b6675" }}>Nodal officer, signed in · <button className="linkbtn" type="button" onClick={() => setOfficer("")}>Sign out</button></span></span>
            </div>
          ) : (
            <div className="who"><a className="btn" href="#/signin">Sign in</a></div>
          )}
        </div>
      </header>
      {body}
    </div>
  );
}