import { FormEvent, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { DomainState, RunCreated, Status } from '../api/types';
import { ResultBadge, SeverityBadge, TierStateBadge } from '../components/Badges';
import { Glyph } from '../components/Glyph';
import { ErrorBox, Loading, TableWrap } from '../components/States';
import { Icons } from '../lib/icons';
import { useAsync, useTitle, toApiError } from '../hooks';
import type { ApiError } from '../api/client';
import { SEVERITY_ORDER, TIERS, formatDateTime, formatUsd, humanize, ratioToPercent } from '../lib/format';

// A domain or overall state gets its own shape as well as its words: never colour alone.
const LABEL_ICON = {
  open_findings: Icons.Triangle,
  no_open_findings: Icons.CheckCircle,
  incomplete_data: Icons.TriangleOutline,
} as const;
const labelIcon = (kind: string) => (kind in LABEL_ICON ? LABEL_ICON[kind as keyof typeof LABEL_ICON] : Icons.Square);
const LABEL_KIND_TEXT: Record<string, string> = {
  open_findings: 'Open findings',
  no_open_findings: 'No open findings',
  incomplete_data: 'Incomplete data',
};

export function HomePage() {
  useTitle('Status');
  const [params, setParams] = useSearchParams();
  const period = params.get('period') ?? undefined;
  const asOf = params.get('as_of') ?? undefined;
  const { data, error, loading, reload } = useAsync(() => api.status({ period, as_of: asOf }), [period, asOf]);
  const [periodInput, setPeriodInput] = useState(period ?? '');

  function applyPeriod(e: FormEvent) {
    e.preventDefault();
    const next = new URLSearchParams(params);
    if (periodInput.trim()) next.set('period', periodInput.trim());
    else next.delete('period');
    setParams(next);
  }

  return (
    <>
      <div className="page-head">
        <h1>Status</h1>
        <form className="inline-form" onSubmit={applyPeriod}>
          <label htmlFor="period-view">Period (YYYY-MM)</label>
          <input
            id="period-view"
            name="period"
            value={periodInput}
            onChange={(e) => setPeriodInput(e.target.value)}
            placeholder={data?.period ?? 'latest'}
            pattern="\d{4}-\d{2}"
            inputMode="numeric"
          />
          <button type="submit">Show period</button>
        </form>
      </div>

      {error ? <ErrorBox error={error} onRetry={reload} /> : !data ? <Loading what="status" /> : <StatusBody status={data} onRunDone={reload} loading={loading} />}
    </>
  );
}

function StatusBody({ status, onRunDone, loading }: { status: Status; onRunDone: () => void; loading: boolean }) {
  const sevKeys = [...SEVERITY_ORDER, ...Object.keys(status.by_severity).filter((k) => !SEVERITY_ORDER.includes(k))].filter(
    (k) => k in status.by_severity,
  );
  const enabledNames = status.domains.filter((d) => d.enabled && d.available).map((d) => d.name);
  const domainName = (id: string) => status.domains.find((d) => d.id === id)?.name ?? id;
  return (
    <>
      <p className="meta">
        Period <strong>{status.period}</strong> {'·'} as of {status.as_of}
        {loading ? <span role="status"> {'·'} Updating{'…'}</span> : null}
      </p>

      <section aria-labelledby="overall-heading" className="card overall">
        <h2 id="overall-heading" className="visually-hidden">
          Overall
        </h2>
        <p className={`big-label kind-${status.label_kind}`}>
          <Glyph icon={labelIcon(status.label_kind)} size={34} /> <span>{status.label}</span>
        </p>
        <p className="kind-text">{LABEL_KIND_TEXT[status.label_kind] ?? humanize(status.label_kind)}</p>
        <p className="muted small scope-note">
          Across enabled domains: {enabledNames.length ? enabledNames.join(', ') : 'none'}. Counts, exposure and coverage below span these domains.
        </p>
        {/* The bar is a picture of the counts beside it: each segment grows in proportion to its own count, and every figure is shown as text. */}
        <div className="sevbar" aria-hidden="true">
          {sevKeys.map((k) => (
            <span key={k} className={`sevbar-seg sev-${k}`} style={{ flexGrow: status.by_severity[k] }} />
          ))}
        </div>

        <dl className="facts">
          <div>
            <dt>Open findings by severity</dt>
            <dd>
              <ul className="inline-list">
                {sevKeys.map((k) => (
                  <li key={k}>
                    <SeverityBadge severity={k} /> <span className="count">{status.by_severity[k]}</span>
                  </li>
                ))}
              </ul>
            </dd>
          </div>
          <div>
            <dt>Total exposure</dt>
            <dd className="fact-big">{formatUsd(status.total_exposure_usd)}</dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>
              <span className="fact-big">
                {status.coverage.evaluated} of {status.coverage.applicable} rules evaluated
              </span>
              <br />
              <span className="muted">
                Coverage ratio {status.coverage.ratio} {'·'} floor {status.coverage.floor}
              </span>
              <Meter ratio={status.coverage.ratio} floor={status.coverage.floor} />
            </dd>
          </div>
          <div>
            <dt>Last run</dt>
            <dd>
              {status.last_run_id ? <Link to={`/runs?run=${encodeURIComponent(status.last_run_id)}`}>{status.last_run_id}</Link> : 'No run yet'}
              {status.last_run_at ? <span className="muted"> {'·'} {formatDateTime(status.last_run_at)}</span> : null}
            </dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="domains-heading">
        <h2 id="domains-heading">Domains</h2>
        <ul className="domain-list">
          {status.domains.map((d) => (
            <DomainTile key={d.id} domain={d} />
          ))}
        </ul>
      </section>

      <section aria-labelledby="tiers-heading">
        <h2 id="tiers-heading">Run cadence by tier</h2>
        <TableWrap label="Run cadence by tier">
          <table>
            <caption className="visually-hidden">State of each run tier against its declared cadence</caption>
            <thead>
              <tr>
                <th scope="col">Tier</th>
                <th scope="col">Cadence</th>
                <th scope="col">Last run</th>
                <th scope="col">Rules evaluated</th>
                <th scope="col">State</th>
                <th scope="col">Next due</th>
                <th scope="col">Note</th>
              </tr>
            </thead>
            <tbody>
              {status.tiers.map((t) => (
                <tr key={t.tier}>
                  <th scope="row">{t.label}</th>
                  <td>{humanize(t.cadence)}</td>
                  <td>{t.last_run_at ? formatDateTime(t.last_run_at) : 'Never'}</td>
                  <td>
                    {t.rules_evaluated} of {t.rules_in_tier}
                  </td>
                  <td>
                    <TierStateBadge state={t.state} />
                  </td>
                  <td>{t.next_due ?? '—'}</td>
                  <td>{t.note ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
        <p className="oldest-source">
          {status.oldest_source ? (
            <>
              <strong>Oldest source:</strong> {status.oldest_source.source} ({status.oldest_source.file}), data as of{' '}
              {status.oldest_source.data_as_of}, {status.oldest_source.age_days} days old. This bounds how current any finding can be.
            </>
          ) : (
            <>
              <strong>Oldest source:</strong> no source data has been loaded yet.
            </>
          )}
        </p>
      </section>

      <section aria-labelledby="rules-heading">
        <h2 id="rules-heading">Rule results</h2>
        <ul className="rule-results">
          {status.rules.map((r) => (
            <li key={r.rule_id}>
              <strong>{r.rule_id}</strong> <ResultBadge result={r.result} /> <span className="muted small">{domainName(r.domain)}</span>
              {r.reason ? <span className="muted"> {r.reason}</span> : null}
            </li>
          ))}
        </ul>
      </section>

      <RunNow period={status.period} onDone={onRunDone} />
    </>
  );
}

/** Coverage as a bar with the floor marked on it. Purely visual: the figures are printed beside it, as the API sent them. */
function Meter({ ratio, floor }: { ratio: string; floor: string }) {
  return (
    <span className="meter" aria-hidden="true">
      <span className="meter-fill" style={{ width: ratioToPercent(ratio) }} />
      <span className="meter-floor" style={{ left: ratioToPercent(floor) }} />
    </span>
  );
}

/** One domain tile. Enabled and available: a link to that domain's queue. Otherwise plain text: "Not enabled" or "Coming later". */
function DomainTile({ domain: d }: { domain: DomainState }) {
  const live = d.enabled && d.available;
  const kind = d.label_kind ?? '';
  if (!live) {
    const text = d.available ? d.state : humanize(d.state); // "Not enabled" | "coming later" -> "Coming later"
    return (
      <li className="domain later">
        <span className="domain-name">{d.name}</span>
        <span className="domain-state">
          <Glyph icon={Icons.DashedCircle} size={15} />
          {text}
        </span>
      </li>
    );
  }
  const sevKeys = [...SEVERITY_ORDER, ...Object.keys(d.by_severity).filter((k) => !SEVERITY_ORDER.includes(k))].filter((k) => k in d.by_severity);
  return (
    <li className={`domain active kind-${kind}`}>
      <Link to={`/findings?domain=${encodeURIComponent(d.id)}`} className="domain-link">
        <span className="domain-name">{d.name}</span>
        <span className="visually-hidden">: </span>
        <span className="domain-state">
          <Glyph icon={labelIcon(kind)} size={15} />
          {d.state}
        </span>
      </Link>
      <dl className="domain-facts">
        <div>
          <dt>Open findings</dt>
          <dd className="count">{d.open_findings}</dd>
        </div>
        <div>
          <dt>By severity</dt>
          <dd>
            <ul className="inline-list">
              {sevKeys.map((k) => (
                <li key={k}>
                  <SeverityBadge severity={k} /> <span className="count">{d.by_severity[k]}</span>
                </li>
              ))}
            </ul>
          </dd>
        </div>
        <div>
          <dt>Coverage</dt>
          <dd>
            {d.coverage ? (
              <>
                {d.coverage.evaluated} of {d.coverage.applicable} rules evaluated
                <span className="muted small">
                  {' '}
                  {'·'} ratio {d.coverage.ratio} {'·'} floor {d.coverage.floor}
                </span>
                <Meter ratio={d.coverage.ratio} floor={d.coverage.floor} />
              </>
            ) : (
              'Not reported'
            )}
          </dd>
        </div>
      </dl>
    </li>
  );
}

function RunNow({ period, onDone }: { period: string; onDone: () => void }) {
  const [tier, setTier] = useState('close');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | undefined>();
  const [result, setResult] = useState<RunCreated | undefined>();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    setResult(undefined);
    try {
      const r = await api.createRun(period, tier);
      setResult(r);
      onDone();
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="run-heading" className="card run-now">
      <h2 id="run-heading">Run now</h2>
      <form className="inline-form" onSubmit={submit}>
        <p className="form-fixed">
          Period: <strong>{period}</strong>
        </p>
        <label htmlFor="run-tier">Tier</label>
        <select id="run-tier" value={tier} onChange={(e) => setTier(e.target.value)}>
          {TIERS.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
        <button type="submit" disabled={busy}>
          {busy ? 'Running…' : 'Run now'}
        </button>
      </form>
      {error ? <ErrorBox error={error} /> : null}
      {result ? (
        <div role="status" className="state state-ok">
          <p>
            <Glyph icon={Icons.CheckCircle} size={16} />
            Run <Link to={`/runs?run=${encodeURIComponent(result.run_id)}`}>{result.run_id}</Link> finished: {result.label}.
          </p>
          <p className="muted">
            {humanize(result.tier)} tier {'·'} period {result.period} {'·'} {result.findings} findings, {result.new_findings} new {'·'} exposure{' '}
            {formatUsd(result.total_exposure_usd)} {'·'} {formatDateTime(result.executed_at)}
          </p>
        </div>
      ) : null}
    </section>
  );
}
