import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { ApiError } from '../api/client';
import type { ReplayResult, RunDetail } from '../api/types';
import { ResultBadge } from '../components/Badges';
import { Empty, ErrorBox, Loading, TableWrap } from '../components/States';
import { Glyph } from '../components/Glyph';
import { toApiError, useAsync, useTitle } from '../hooks';
import { Icons } from '../lib/icons';
import { dash, formatDateTime, formatUsd, humanize } from '../lib/format';

export function RunsPage() {
  useTitle('Runs');
  const [params, setParams] = useSearchParams();
  const selected = params.get('run') ?? '';
  const list = useAsync(() => api.runs(), []);

  function select(id: string) {
    const next = new URLSearchParams(params);
    next.set('run', id);
    setParams(next);
  }

  return (
    <>
      <h1>Runs</h1>
      {list.error ? (
        <ErrorBox error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <Loading what="runs" />
      ) : list.data.items.length === 0 ? (
        <Empty>No runs yet. Use Run now on the Status page to start one.</Empty>
      ) : (
        <TableWrap label="Run list">
          <table>
            <caption className="visually-hidden">Runs, as returned by the API</caption>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Period</th>
                <th scope="col">Tier</th>
                <th scope="col">Executed</th>
                <th scope="col">Result</th>
                <th scope="col" className="num">
                  Findings
                </th>
                <th scope="col" className="num">
                  Exposure
                </th>
                <th scope="col" className="num">
                  Config version
                </th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((r) => (
                <tr key={r.run_id} className={r.run_id === selected ? 'selected' : undefined}>
                  <th scope="row">
                    <button type="button" className="link-button" aria-pressed={r.run_id === selected} onClick={() => select(r.run_id)}>
                      {r.run_id}
                    </button>
                  </th>
                  <td>{r.period}</td>
                  <td>{humanize(r.tier)}</td>
                  <td>{formatDateTime(r.executed_at)}</td>
                  <td>{r.label}</td>
                  <td className="num">{r.findings}</td>
                  <td className="num">{formatUsd(r.total_exposure_usd)}</td>
                  <td className="num">{r.config_version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
      {selected ? <RunDetailView id={selected} /> : list.data && list.data.items.length > 0 ? <p className="muted">Select a run to see its manifest and per-rule results.</p> : null}
    </>
  );
}

function RunDetailView({ id }: { id: string }) {
  const { data, error, reload } = useAsync(() => api.run(id), [id]);
  return (
    <section aria-labelledby="run-detail-heading" className="run-detail">
      <h2 id="run-detail-heading">Run {id}</h2>
      {error ? <ErrorBox error={error} onRetry={reload} /> : !data ? <Loading what="run" /> : <RunBody run={data} />}
    </section>
  );
}

function RunBody({ run }: { run: RunDetail }) {
  const m = run.manifest;
  const enabledDomains = m.enabled_domains ?? [];
  const refTables = m.reference_tables ?? [];
  return (
    <>
      <p className="meta">
        {humanize(run.tier)} tier {'·'} period {run.period} {'·'} executed {formatDateTime(run.executed_at)} {'·'} <strong>{run.label}</strong> {'·'}{' '}
        exposure {formatUsd(run.total_exposure_usd)}
      </p>
      <p>
        Findings in this run:{' '}
        {run.findings.length === 0
          ? 'none'
          : run.findings.map((f, i) => (
              <span key={f}>
                {i > 0 ? ', ' : ''}
                <Link to={`/findings/${encodeURIComponent(f)}`}>{f}</Link>
              </span>
            ))}
      </p>

      <Replay runId={run.run_id} />

      <h3>Per-rule results</h3>
      <TableWrap label="Per-rule results">
        <table>
          <caption className="visually-hidden">Result of each rule in this run</caption>
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Domain</th>
              <th scope="col">Version</th>
              <th scope="col">Result</th>
              <th scope="col" className="num">
                Findings
              </th>
              <th scope="col">Metrics</th>
              <th scope="col">Reason</th>
            </tr>
          </thead>
          <tbody>
            {run.rules.map((r) => (
              <tr key={r.rule_id}>
                <th scope="row">{r.rule_id}</th>
                <td>{r.domain ? <code>{r.domain}</code> : '—'}</td>
                <td>{dash(m.rule_versions[r.rule_id])}</td>
                <td>
                  <ResultBadge result={r.result} />
                </td>
                <td className="num">{r.findings}</td>
                <td>
                  {r.metrics.length === 0
                    ? '—'
                    : r.metrics.map((x) => (
                        <div key={x.id}>
                          {x.id}: {x.value} <span className="muted">({x.result})</span>
                        </div>
                      ))}
                </td>
                <td>{r.reason ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>

      <h3>Manifest</h3>
      <h4>Inputs</h4>
      <TableWrap label="Manifest inputs">
        <table>
          <caption className="visually-hidden">Input files pinned by this run</caption>
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col">File</th>
              <th scope="col">sha256</th>
              <th scope="col" className="num">
                Rows
              </th>
              <th scope="col">Data as of</th>
            </tr>
          </thead>
          <tbody>
            {m.inputs.map((i) => (
              <tr key={i.source}>
                <th scope="row">{i.source}</th>
                <td>
                  <code>{i.file}</code>
                </td>
                <td>
                  <code>{i.sha256}</code>
                </td>
                <td className="num">{i.rows}</td>
                <td>{i.data_as_of}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableWrap>
      <p>
        <strong>Sources not provided:</strong> {m.sources_absent.length ? m.sources_absent.join(', ') : 'none'}
      </p>

      <h4>Reference tables</h4>
      {refTables.length === 0 ? (
        <Empty>This run pinned no reference tables.</Empty>
      ) : (
        <TableWrap label="Manifest reference tables">
          <table>
            <caption className="visually-hidden">Reference tables pinned by this run</caption>
            <thead>
              <tr>
                <th scope="col">Table</th>
                <th scope="col">sha256</th>
              </tr>
            </thead>
            <tbody>
              {refTables.map((t) => (
                <tr key={t.name}>
                  <th scope="row">{t.name}</th>
                  <td>
                    <code>{t.sha256}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}

      <dl className="facts compact manifest-facts">
        <div>
          <dt>Enabled domains</dt>
          <dd>{enabledDomains.length ? enabledDomains.map((d, i) => <span key={d}>{i > 0 ? ', ' : ''}<code>{d}</code></span>) : 'none recorded'}</dd>
        </div>
        <div>
          <dt>Configuration</dt>
          <dd>
            version {m.config_file.config_version} {'·'} sha256 <code>{m.config_file.sha256}</code>
          </dd>
        </div>
        <div>
          <dt>Floor registry version</dt>
          <dd>{m.floor_registry_version}</dd>
        </div>
        <div>
          <dt>Materiality</dt>
          <dd>
            {formatUsd(m.materiality.usd)}{' '}
            <span className="muted">
              (percentage {m.materiality.pct}, basis {formatUsd(m.materiality.basis_usd)})
            </span>
          </dd>
        </div>
        <div>
          <dt>Declared schedule</dt>
          <dd>{Object.entries(m.declared_schedule).map(([k, v]) => `${humanize(k)}: ${humanize(v).toLowerCase()}`).join(' · ')}</dd>
        </div>
        <div>
          <dt>Mapping versions</dt>
          <dd>{Object.entries(m.mapping_versions).map(([k, v]) => `${k} v${v}`).join(' · ')}</dd>
        </div>
        <div>
          <dt>Rule versions</dt>
          <dd>{Object.entries(m.rule_versions).map(([k, v]) => `${k} v${v}`).join(' · ')}</dd>
        </div>
      </dl>
    </>
  );
}

function Replay({ runId }: { runId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReplayResult | undefined>();
  const [error, setError] = useState<ApiError | undefined>();

  async function go() {
    setBusy(true);
    setResult(undefined);
    setError(undefined);
    try {
      setResult(await api.replay(runId));
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="replay">
      <button type="button" onClick={go} disabled={busy}>
        {busy ? 'Replaying…' : 'Replay this run'}
      </button>
      <span className="muted small"> Re-executes the run from its pinned inputs and compares the findings.</span>
      {error ? <ErrorBox error={error} /> : null}
      {result ? (
        <div role="status" className={`state ${result.identical ? 'state-ok' : 'state-error'}`}>
          <p>
            <strong>
              <Glyph icon={result.identical ? Icons.CheckCircle : Icons.XCircle} size={16} />
              Identical: {result.identical ? 'true' : 'false'}
            </strong>{' '}
            {'·'} {result.findings_compared} findings compared
          </p>
          {result.reason ? <p>Reason: {result.reason}</p> : null}
          {result.diff.length > 0 ? (
            <>
              <h4>Differences</h4>
              <ul>
                {result.diff.map((d, i) => (
                  <li key={i}>{typeof d === 'string' ? d : <code>{JSON.stringify(d)}</code>}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted">No differences.</p>
          )}
        </div>
      ) : null}
    </div>
  );
}
