import { api } from "./api.js";
import { Logo } from "./components/bits.jsx";
import { useLoad, useOfficer, useRoute } from "./hooks.js";
import Case from "./pages/Case.jsx";
import Compare from "./pages/Compare.jsx";
import { Audit, Holds } from "./pages/Ledger.jsx";
import Notices from "./pages/Notices.jsx";
import Queue from "./pages/Queue.jsx";
import Review from "./pages/Review.jsx";

const NAV = [["review", "Review"], ["queue", "Tickets"], ["notices", "Notices"], ["holds", "Hold ledger"], ["compare/CASE_DEMO", "Before / after"], ["audit", "Audit log"]];

export default function App() {
  const route = useRoute();
  const [officer, setOfficer] = useOfficer();
  const config = useLoad(() => api.config(), []);
  const [page, a, b] = route;
  const modelOn = config.data?.model?.enabled;
  const current = page === "case" ? "queue" : page;

  let body;
  if (page === "case" && a) body = <Case key={a} caseId={a} accountId={b} officer={officer} />;
  else if (page === "compare") body = <Compare key={a || "CASE_DEMO"} caseId={a || "CASE_DEMO"} />;
  else if (page === "queue") body = <Queue modelOn={modelOn} />;
  else if (page === "notices") body = <Notices officer={officer} />;
  else if (page === "holds") body = <Holds />;
  else if (page === "audit") body = <Audit />;
  else body = <Review officer={officer} />;

  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <header className="topbar">
        <div className="wrap">
          <a className="brand" href="#/review" style={{ color: "inherit", textDecoration: "none" }}>
            <Logo />
            <div><b>Proportionate Holds</b><span>Bank X · nodal desk</span></div>
          </a>
          <nav className="mainnav" aria-label="Main">
            {NAV.map(([href, label]) => (
              <a key={href} href={`#/${href}`} aria-current={current === href.split("/")[0] ? "page" : undefined}>{label}</a>
            ))}
          </nav>
          <label className="officer">Officer
            <input value={officer} onChange={(e) => setOfficer(e.target.value)} placeholder="Your name" autoComplete="name" />
          </label>
          {modelOn && <span className="pill pill-plain" title="The local model chooses the order of the agent's tools">Model: {config.data.model.name}</span>}
        </div>
      </header>
      {body}
    </>
  );
}