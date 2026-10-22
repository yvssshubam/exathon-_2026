import { clock, inr, inrR, plural } from "../format.js";
import Decide from "./Decide.jsx";
import { api } from "../api.js";

// Mule networks (pipeline/networks.py): accounts that pay each other or cash out at the same place, decided as one.
function why(lk) {
  if (lk.kind === "SHARED_EXIT") return `${lk.from_name} and ${lk.to_name} both cashed out at ${lk.detail}`;
  if (lk.kind === "TRIED") return `${lk.from_name} tried to pay ${lk.to_name} ${inr(lk.amount_inr)} at ${clock(lk.first_at)}; refused, the money is held`;
  const when = lk.count === 1 ? ` at ${clock(lk.first_at)}` : ` in ${lk.count} payments`;
  return `${lk.from_name} paid ${lk.to_name} ${inr(lk.amount_inr)}${when}`;
}

function NetworkCard({ n, waiting, officer, onDone }) {
  const links = n.links.slice(0, 5);
  return (
    <article className="card decision">
      <div className="decision-head">
        <div>
          <h4>Network {n.network_id} · {plural(n.members.length, "account")}</h4>
          <div className="muted">
            Named in {plural(n.tickets.length, "ticket")} · {inrR(n.reported_inr)} reported · {inrR(n.victim_inr)} of victims' money traced in
          </div>
        </div>
        <span className="pill pill-held pill-lg">Mule network</span>
      </div>
      <div className="figures">
        <div><div className="label">Balance across the network</div><div className="val">{inr(n.balance_inr)}</div></div>
        <div><div className="label">Held now, amount only</div><div className="val">{inr(n.held_inr)}</div></div>
        <div><div className="label">Refused since the holds</div><div className="val">{n.refused}</div></div>
      </div>
      <ul className="chips">
        {n.members.map((m) => <li key={m.account_id}><b>{m.name}</b> {inr(m.balance_inr)}</li>)}
      </ul>
      <h5 className="why-h">Why they belong together</h5>
      <ul className="why">{links.map((lk, i) => <li key={i}>{why(lk)}</li>)}</ul>
      {n.links.length > links.length && <p className="note" style={{ margin: "-8px 0 12px" }}>and {plural(n.links.length - links.length, "more link")}</p>}
      <p className="basis">
        Tickets: {n.tickets.map((t, i) => <span key={t.case_id}>{i ? ", " : ""}<a href={`#/case/${t.case_id}`}>{clock(t.received_at)} ({t.case_id})</a></span>)}.
        {" "}One approval freezes every account in the network that has a freeze waiting and covers all of these tickets; the reasons go to the police as one note.
      </p>
      {waiting.length > 0
        ? <Decide proposals={waiting} officer={officer} onDone={onDone}
                  yesLabel={`Freeze all ${plural(new Set(waiting.map((w) => w.account_id)).size, "account")}`}
                  draftFetch={() => api.networkDraft(n.network_id)}
                  coverNote={`This one decision covers ${plural(new Set(waiting.map((w) => w.account_id)).size, "account")} and ${plural(waiting.length, "waiting proposal")} across ${plural(new Set(waiting.map((w) => w.case_id)).size, "ticket")}.`} />
        : <p className="outcome">Decided: nothing in this network is waiting for you.</p>}
    </article>
  );
}

export default function Networks({ networks, waiting, officer, onDone }) {
  const open = (networks || []).map((n) => {
    const ids = new Set(n.members.map((m) => m.account_id));
    return { n, waiting: waiting.filter((w) => w.action_code === "PROPOSE_FULL_FREEZE" && ids.has(w.account_id)) };
  }).filter((x) => x.waiting.length > 0);
  if (!open.length) return null;
  return (
    <div className="group">
      <div className="group-h"><h3>Mule networks: decide each ring at once</h3><span className="count">{open.length}</span></div>
      <p className="group-p">These accounts pay each other or cash out at the same place. The agent linked them across tickets.</p>
      {open.map(({ n, waiting: w }) => <NetworkCard key={n.network_id} n={n} waiting={w} officer={officer} onDone={onDone} />)}
    </div>
  );
}