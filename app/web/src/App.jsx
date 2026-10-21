import { api } from "./api.js";
import { useLoad, useOfficer, useRoute } from "./hooks.js";
import Case from "./pages/Case.jsx";
import Compare from "./pages/Compare.jsx";
import { Audit, Holds } from "./pages/Ledger.jsx";
import Queue from "./pages/Queue.jsx";
import Review from "./pages/Review.jsx";

const NAV = [["review", "Morning review"], ["queue", "Ticket queue"], ["compare/CASE_DEMO", "Compare"], ["holds", "Hold ledger"], ["audit", "Audit log"]];

export default function App() {
  const route = useRoute();
  const [officer, setOfficer] = useOfficer();
  const config = useLoad(() => api.config(), []);
  const [page, a, b] = route;
  const modelOn = config.data?.model?.enabled;

  let body;
  if (page === "case" && a) body = <Case caseId={a} accountId={b} officer={officer} />;
  else if (page === "compare") body = <Compare caseId={a || "CASE_DEMO"} />;
  else if (page === "queue") body = <Queue modelOn={modelOn} />;
  else if (page === "holds") body = <Holds />;
  else if (page === "audit") body = <Audit />;
  else body = <Review officer={officer} />;

  return (
    <div className="app">
      <a className="skip" href="#main">Skip to content</a>
      <nav className="side" aria-label="Main">
        <div className="brand">
          <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true"><rect x="3" y="11" width="26" height="10" rx="3" fill="#2F7A5A" /><rect x="3" y="11" width="9" height="10" rx="3" fill="#8E73D6" /><rect x="9" y="11" width="3" height="10" fill="#8E73D6" /></svg>
          <span>Proportionate Holds<small>Bank X, nodal desk</small></span>
        </div>
        <ul>
          {NAV.map(([href, label]) => (
            <li key={href}><a href={`#/${href}`} aria-current={page === href.split("/")[0] ? "page" : undefined}>{label}</a></li>
          ))}
        </ul>
        <label className="officer">Officer
          <input value={officer} onChange={(e) => setOfficer(e.target.value)} placeholder="Your name" autoComplete="name" />
        </label>
        <p className="side-note">
          {modelOn ? <>Local model on: {config.data.model.name}</> : "Fixed order (no model)"}<br />
          Synthetic data only
        </p>
      </nav>
      <main id="main" tabIndex="-1">{body}</main>
    </div>
  );
}