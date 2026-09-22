import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { FindingDetail } from '../api/types';
import { SeverityBadge, StatusBadge } from '../components/Badges';
import { Disposition } from '../components/Disposition';
import { Evidence } from '../components/Evidence';
import { Empty, ErrorBox, Loading, TableWrap } from '../components/States';
import { useAsync, useTitle } from '../hooks';
import { dash, formatDateTime, formatUsd, humanize } from '../lib/format';

export function FindingDetailPage() {
  const { id = '' } = useParams();
  useTitle(`Finding ${id}`);
  const { data, error, reload } = useAsync(() => api.finding(id), [id]);
  const [finding, setFinding] = useState<FindingDetail | undefined>();

  useEffect(() => setFinding(data), [data]);

  return (
    <>
      <p className="breadcrumb">
        <Link to="/findings">{'←'} All findings</Link>
      </p>
      {error ? <ErrorBox error={error} onRetry={reload} /> : !finding ? <Loading what="finding" /> : <FindingBody finding={finding} onUpdated={setFinding} />}
    </>
  );
}

function FindingBody({ finding: f, onUpdated }: { finding: FindingDetail; onUpdated: (f: FindingDetail) => void }) {
  const ex = f.explanation;
  return (
    <article aria-labelledby="finding-title">
      <header className="finding-head">
        <h1 id="finding-title">
          <span className="finding-id">{f.finding_id}</span> <span className="visually-hidden">: </span>
          <span className="finding-headline">{f.headline}</span>
        </h1>
        <ul className="inline-list finding-meta">
          <li>
            <SeverityBadge severity={f.severity} />
          </li>
          <li>
            <StatusBadge status={f.status} />
          </li>
          <li>
            <strong>{formatUsd(f.exposure_usd)}</strong> exposure
          </li>
          <li>Period {f.period}</li>
          <li>
            Rule {f.rule_id} <span className="muted">v{f.rule_version}</span>
          </li>
          <li>
            Domain <strong>{f.domain_name}</strong>
          </li>
        </ul>
      </header>

      {/* Mandated order (design 8.2): a What happened, b Why it matters, c Impact, d Recommended action,
          then e Evidence, then f Disposition, then g History. Explanation always precedes evidence. */}
      <section aria-labelledby="sec-what" id="what-happened" className="card">
        <h2 id="sec-what">What happened</h2>
        <p>{ex.what_happened}</p>
      </section>

      <section aria-labelledby="sec-why" id="why-it-matters" className="card">
        <h2 id="sec-why">Why it matters</h2>
        <p>{ex.why_it_matters}</p>
        <h3>Authorities cited</h3>
        {f.authorities.length ? (
          <ul>
            {f.authorities.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">No authority is cited for this rule.</p>
        )}
        <p className="muted small">
          Basis: {humanize(f.basis)} {'·'} <Link to="/rules">See this rule and its thresholds</Link>
        </p>
      </section>

      <section aria-labelledby="sec-impact" id="impact" className="card">
        <h2 id="sec-impact">Impact</h2>
        <p>{ex.impact}</p>
        <dl className="facts compact">
          <div>
            <dt>Exposure</dt>
            <dd>
              {formatUsd(f.exposure_usd)}
              {/* a rule that assigns no dollars (basis "none") has nothing to explain in brackets */}
              {f.exposure_basis && f.exposure_basis !== 'none' && <span className="muted"> ({humanize(f.exposure_basis)})</span>}
            </dd>
          </div>
          <div>
            <dt>Employees affected</dt>
            <dd>{f.affected.employees.length ? f.affected.employees.join(', ') : 'None'}</dd>
          </div>
          <div>
            <dt>Contracts affected</dt>
            <dd>{f.affected.contracts.length ? f.affected.contracts.join(', ') : 'None'}</dd>
          </div>
          <div>
            <dt>Entries affected</dt>
            <dd>{f.affected.entries}</dd>
          </div>
        </dl>
        <details>
          <summary>Values the rule computed</summary>
          <dl className="detail-list">
            {Object.entries(f.computed).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{dash(v)}</dd>
              </div>
            ))}
            {f.metric ? (
              <>
                <div>
                  <dt>metric {f.metric.id}</dt>
                  <dd>
                    {f.metric.value} ({f.metric.numerator} of {f.metric.denominator})
                  </dd>
                </div>
                <div>
                  <dt>threshold tripped</dt>
                  <dd>{f.metric.threshold_tripped}</dd>
                </div>
                <div>
                  <dt>baseline confidence</dt>
                  <dd>{f.metric.baseline_confidence}</dd>
                </div>
              </>
            ) : null}
            <div>
              <dt>severity reason</dt>
              <dd>{f.severity_reason}</dd>
            </div>
          </dl>
        </details>
      </section>

      <section aria-labelledby="sec-action" id="recommended-action" className="card">
        <h2 id="sec-action">Recommended action</h2>
        <p>{ex.recommended_action}</p>
      </section>

      <Evidence findingId={f.finding_id} />

      <Disposition finding={f} onUpdated={onUpdated} />

      <section aria-labelledby="sec-history" id="history">
        <h2 id="sec-history">History</h2>
        {f.history.length === 0 ? (
          <Empty>No status changes have been recorded.</Empty>
        ) : (
          <TableWrap label="Status history">
            <table>
              <caption className="visually-hidden">Every status change, with who made it and when</caption>
              <thead>
                <tr>
                  <th scope="col">When</th>
                  <th scope="col">Change</th>
                  <th scope="col">By</th>
                  <th scope="col">Reason code</th>
                  <th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {f.history.map((h, i) => (
                  <tr key={`${h.at}-${i}`}>
                    <th scope="row">{formatDateTime(h.at)}</th>
                    <td>
                      {h.from ? <StatusBadge status={h.from} /> : <span className="muted">Created</span>} <span aria-hidden="true">{'→'}</span>
                      <span className="visually-hidden"> to </span> <StatusBadge status={h.to} />
                    </td>
                    <td>{h.actor}</td>
                    <td>{h.reason_code ? humanize(h.reason_code) : '—'}</td>
                    <td>{h.note ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </section>
    </article>
  );
}
