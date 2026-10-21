import { inr } from "../format.js";

export function Loading({ what = "Loading" }) {
  return <p className="quiet" role="status">{what}…</p>;
}

export function Failed({ error, retry }) {
  return (
    <div className="failed" role="alert">
      <p>{String(error?.message || error)}</p>
      <p className="quiet">Is the API running? Start it with <code>python -m app.api</code> and Exasol with <code>exasol start</code>.</p>
      {retry && <button className="btn" onClick={retry}>Try again</button>}
    </div>
  );
}

// Stacked bar of where the victim's money is. Colour never carries meaning alone: every segment has a legend line.
export function MoneyBar({ parts, total, caption }) {
  const sum = total || parts.reduce((s, p) => s + (p.value || 0), 0) || 1;
  return (
    <figure className="moneybar">
      <div className="moneybar-track" aria-hidden="true">
        {parts.filter((p) => p.value > 0).map((p) => (
          <span key={p.key} className={`seg seg-${p.key}`} style={{ width: `${(100 * p.value) / sum}%` }} />
        ))}
      </div>
      <figcaption>
        {caption && <span className="moneybar-caption">{caption}</span>}
        <ul className="legend">
          {parts.map((p) => (
            <li key={p.key}><span className={`swatch seg-${p.key}`} aria-hidden="true" />{p.label} <b className="num">{inr(p.value)}</b></li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}

export function Stmt({ ids }) {
  if (!ids && ids !== 0) return null;
  return <span className="stmt" title="Exasol statement that produced this">stmt {String(ids)}</span>;
}

export function NightBadge({ on }) {
  return on ? <span className="badge badge-night" title="Outside desk hours">desk closed</span> : null;
}