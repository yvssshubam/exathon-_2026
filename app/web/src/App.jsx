import { api } from "./api.js";
import { Logo } from "./components/bits.jsx";
import { useLoad, useOfficer, useRoute } from "./hooks.js";
import Case from "./pages/Case.jsx";
import Compare from "./pages/Compare.jsx";
import { Audit, Holds } from "./pages/Ledger.jsx";
import Narrator from "./pages/Narrator.jsx";
import Queue from "./pages/Queue.jsx";
import Record from "./pages/Record.jsx";
import Review from "./pages/Review.jsx";

// Bank X's nodal desk. "The whole night" is the demo's narrator view: no bank's officer has that screen.
const NAV = [["review", "This morning"], ["queue", "Tickets"], ["holds", "Hold ledger"],
  ["compare/CASE_DEMO", "Before / after"], ["audit", "Audit log"], ["narrator", "The whole night"]];

export default function App() {
  const route = useRoute();
  const [officer, setOfficer] = useOfficer();
  const config = useLoad(() => api.config(), []);
  const [page, a, b] = route;
  const modelOn = config.data?.model?.enabled;
  const current = page === "case" ? "queue" : page === "record" ? "holds" : page;

  let body;
  if (page === "case" && a) body = <Case key={a} caseId={a} accountId={b} officer={officer} />;
  else if (page === "record" && a) body = <Record key={a} accountId={a} />;
  else if (page === "compare") body = <Compare key={a || "CASE_DEMO"} caseId={a || "CASE_DEMO"} />;
  else if (page === "queue") body = <Queue modelOn={modelOn} />;
  else if (page === "holds") body = <Holds />;
  else if (page === "audit") body = <Audit />;
  else if (page === "narrator") body = <Narrator />;
  else body = <Review officer={officer} />;

  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <header className={`topbar${page === "narrator" ? " narrator-bar" : ""}`}>
        <div className="wrap">
          <a className="brand" href="#/review" style={{ color: "inherit", textDecoration: "none" }}>
            <Logo />
            <div><b>Proportionate Holds</b><span>{page === "narrator" ? "Narrator's view · every bank" : "Bank X · nodal desk"}</span></div>
          </a>
          <nav className="mainnav" aria-label="Main">
            {NAV.map(([href, label]) => (
              <a key={href} href={`#/${href}`} aria-current={current === href.split("/")[0] ? "page" : undefined}>{label}</a>
            ))}
          </nav>
          <label className="officer">Officer
            <input value={officer} onChange={(e) => setOfficer(e.target.value)} placeholder="Your name" autoComplete="name" />
          </label>
          {modelOn && <span className="pill pill-plain" title="A local model chooses the order of the agent's tools and words the reasons; the checker decides">Model: {config.data.model.name}</span>}
        </div>
      </header>
      {body}
    </>
  );
}