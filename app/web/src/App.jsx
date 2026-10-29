import { useState } from "react";
import { api, SNAPSHOT } from "./api.js";
import { Logo } from "./components/bits.jsx";
import { clock, plural } from "./format.js";
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

function DemoBar({ page, onRan }) {
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const view = page === "compare" ? "compare" : page === "narrator" ? "narrator" : "console";
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
      <div className="wrap" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: "8px 20px", paddingTop: 6, paddingBottom: 6 }}>
        <span className="db-label"><span className="db-dot" aria-hidden="true" />Demo mode</span>
        <nav className="seg" aria-label="Demo views">
          <a className={view === "console" ? "on" : ""} href="#/review" aria-current={view === "console" ? "page" : undefined}>Officer console</a>
          <a className={view === "compare" ? "on" : ""} href="#/compare" aria-current={view === "compare" ? "page" : undefined}>Manual vs system</a>
          <a className={view === "narrator" ? "on" : ""} href="#/narrator" aria-current={view === "narrator" ? "page" : undefined}>The whole night</a>
        </nav>
        {page === "review" || page === "" ? (
          <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            <button className="db-act" type="button" disabled={!!busy} onClick={() => run("night", () => api.night(MORNING, true),
              (r) => `The night ran from ${clock(r.from)} to ${clock(r.until)}: ${plural(r.events, "event")} across the three banks, in ${r.seconds} s.`)}>
              {busy === "night" ? "Running the night…" : "Replay the night to 09:00"}</button>
            <button className="db-act" type="button" disabled={!!busy} onClick={() => run("day", () => api.night(DAY_END, false),
              (r) => `Ran on to ${clock(r.until)}: every complaint of 18 Oct arrived and was handled (${plural(r.events, "event")}, ${r.seconds} s).`)}>
              {busy === "day" ? "Running the rest of the day…" : "Run the rest of the day"}</button>
            <button className="db-act" type="button" disabled={!!busy} onClick={() => run("reset", () => api.reset(), () => "Reset: every complaint waits on the portal again.")}>
              {busy === "reset" ? "Resetting…" : "Reset"}</button>
          </span>
        ) : <span>{CAPTION[view] || "Judges see this bar. Officers never do."}</span>}
      </div>
      {note && <div className="wrap" role="status" style={{ paddingBottom: 8, color: "#e4e9f0" }}>{note}</div>}
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
  const [page, a, b] = route;
  const waiting = inbox.data?.waiting?.length ?? null;
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