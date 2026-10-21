export function Loading({ what = "Loading" }) {
  return <p className="loading" role="status">{what}…</p>;
}

export function Failed({ error, retry }) {
  return (
    <div className="failed" role="alert">
      <p>{String(error?.message || error)}</p>
      {(!error?.status || error.status >= 500) && (
        <p className="note">Is the API running? Start it with <code>python -m app.api</code> and Exasol with <code>exasol start</code>.</p>
      )}
      {retry && <p><button className="btn btn-sm" onClick={retry}>Try again</button></p>}
    </div>
  );
}

export function Stmt({ ids }) {
  if (!ids && ids !== 0) return null;
  return <span className="stmt" title="Exasol statement that produced this">stmt {String(ids)}</span>;
}

export function Logo({ size = 26 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="3" y="11" width="26" height="10" rx="3" fill="#2F7A5A" />
      <rect x="3" y="11" width="9" height="10" rx="3" fill="#5B3FA0" />
      <rect x="9" y="11" width="3" height="10" fill="#5B3FA0" />
    </svg>
  );
}