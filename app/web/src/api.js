// Every call the console makes. The API holds no rules of its own: it runs the night, the agent and the checker,
// and carries out the officer's four decisions. The console is Bank X's nodal desk; the narrator page is the demo's.
const BANK = "BANK_X";

// A saved copy (python -m app.snapshot) carries every answer inside the page and has no API behind it.
export const SNAPSHOT = typeof window !== "undefined" ? window.__PH_SNAPSHOT__ || null : null;

async function call(method, path, body) {
  if (SNAPSHOT) {
    if (method !== "GET") throw new Error("This is a saved copy of the console, so nothing can be changed here. Decisions and the demo controls work in the live console.");
    if (path in SNAPSHOT.data) return structuredClone(SNAPSHOT.data[path]);
    throw Object.assign(new Error("This page was not part of the saved copy."), { status: 404 });
  }
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(`/api${path}${sep}bank=${BANK}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
    throw Object.assign(new Error(detail || `The API answered ${res.status}`), { status: res.status });
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  config: () => call("GET", "/config"),
  clock: () => call("GET", "/clock"),
  // the demo: run the simulated night (every bank, one clock), reset, the narrator's view
  night: (until, reset = false, mode = "pipeline") => call("POST", "/night/run", { until, reset, mode }),
  reset: () => call("POST", "/demo/reset"),
  narrator: (victim = "V_LAKSHMI") => call("GET", `/narrator?victim=${enc(victim)}`),
  // the officer's morning
  inbox: () => call("GET", "/inbox"),
  watch: () => call("GET", "/watch"),
  approve: (proposalId, body) => call("POST", `/proposals/${proposalId}/approve`, body),
  reject: (proposalId, body) => call("POST", `/proposals/${proposalId}/reject`, body),
  undo: (decisionId, body) => call("POST", `/decisions/${decisionId}/undo`, body),
  story: () => call("GET", "/story"),
  record: (acc) => call("GET", `/accounts/${enc(acc)}/record`),
  recordTextUrl: (acc) => SNAPSHOT
    ? `data:text/plain;charset=utf-8,${enc(SNAPSHOT.text[`/accounts/${enc(acc)}/record.txt`] || "")}`
    : `/api/accounts/${enc(acc)}/record.txt?bank=${BANK}`,
  // tickets and cases
  queue: () => call("GET", "/queue"),
  workOne: (caseId, mode = "pipeline") => call("POST", `/tickets/${enc(caseId)}/work?mode=${mode}`),
  caseDetail: (caseId) => call("GET", `/cases/${enc(caseId)}`),
  trail: (caseId) => call("GET", `/cases/${enc(caseId)}/trail`),
  actions: (caseId) => call("GET", `/cases/${enc(caseId)}/actions`),
  account: (caseId, acc) => call("GET", `/cases/${enc(caseId)}/accounts/${enc(acc)}`),
  log: (caseId) => call("GET", `/cases/${enc(caseId)}/log`),
  paperwork: (caseId) => call("GET", `/cases/${enc(caseId)}/paperwork`),
  compare: (caseId, refresh = false) => call("GET", `/cases/${enc(caseId)}/compare${refresh ? "?refresh=true" : ""}`),
  day: () => call("GET", "/day"),
  holds: () => call("GET", "/holds"),
  decisions: () => call("GET", "/decisions"),
  audit: () => call("GET", "/audit"),
  // police notices by e-mail or letter (D-52) and pre-alerts for likely unreported victims (D-53)
  notices: () => call("GET", "/notices"),
  readNotice: (body) => call("POST", "/notices", body),
  prealerts: () => call("GET", "/prealerts"),
};