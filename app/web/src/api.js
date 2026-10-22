// Every call the console makes. The API holds no rules of its own; it runs the agent and the officer actions.
const BANK = "BANK_X";

async function call(method, path, body) {
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

export const api = {
  config: () => call("GET", "/config"),
  queue: () => call("GET", "/queue"),
  inbox: () => call("GET", "/inbox"),
  workOne: (caseId, mode = "pipeline") => call("POST", `/tickets/${caseId}/work?mode=${mode}`),
  workQueue: (mode = "pipeline") => call("POST", `/queue/work?mode=${mode}`),
  caseDetail: (caseId) => call("GET", `/cases/${caseId}`),
  trail: (caseId) => call("GET", `/cases/${caseId}/trail`),
  actions: (caseId) => call("GET", `/cases/${caseId}/actions`),
  account: (caseId, acc) => call("GET", `/cases/${caseId}/accounts/${encodeURIComponent(acc)}`),
  log: (caseId) => call("GET", `/cases/${caseId}/log`),
  paperwork: (caseId) => call("GET", `/cases/${caseId}/paperwork`),
  compare: (caseId, refresh = false) => call("GET", `/cases/${caseId}/compare${refresh ? "?refresh=true" : ""}`),
  approve: (caseId, acc, body) => call("POST", `/cases/${caseId}/accounts/${encodeURIComponent(acc)}/approve`, body),
  reject: (caseId, acc, body) => call("POST", `/cases/${caseId}/accounts/${encodeURIComponent(acc)}/reject`, body),
  sendKyc: (caseId, entryId, body) => call("POST", `/cases/${caseId}/paperwork/${entryId}/send`, body),
  day: () => call("GET", "/day"),
  watch: () => call("GET", "/watch"),
  networks: () => call("GET", "/networks"),
  networkDraft: (id) => call("GET", `/networks/${id}/reasons-draft`),
  reasonsDraft: (acc) => call("GET", `/accounts/${encodeURIComponent(acc)}/reasons-draft`),
  watchRun: (mode = "pipeline") => call("POST", `/watch/run?mode=${mode}`),
  holds: () => call("GET", "/holds"),
  decisions: () => call("GET", "/decisions"),
  audit: () => call("GET", "/audit"),
  reset: () => call("POST", "/demo/reset"),
};