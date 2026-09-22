import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { SeverityBadge, StatusBadge } from '../components/Badges';
import { Empty, ErrorBox, Loading, TableWrap } from '../components/States';
import { useAsync, useTitle } from '../hooks';
import { SEVERITY_LABEL, SEVERITY_ORDER, STATUS_LABEL, STATUS_ORDER, formatUsd } from '../lib/format';

export function FindingsPage() {
  useTitle('Findings');
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const severity = params.get('severity') ?? '';
  const rule = params.get('rule') ?? '';
  const status = params.get('status') ?? '';
  const domain = params.get('domain') ?? '';

  const list = useAsync(() => api.findings({ severity, rule, status, domain }), [severity, rule, status, domain]);
  const rules = useAsync(() => api.rules(), []);

  // Rule choices: the catalog plus any rule id seen on a finding (e.g. data-quality rules), kept across filter changes.
  const [seen, setSeen] = useState<string[]>([]);
  useEffect(() => {
    if (!list.data) return;
    setSeen((prev) => Array.from(new Set([...prev, ...list.data!.items.map((i) => i.rule_id)])));
  }, [list.data]);
  const ruleIds = Array.from(new Set([...(rules.data?.items.map((r) => r.id) ?? []), ...seen])).sort();

  // Domain choices come from the data too: the catalog, plus any domain seen on a finding, plus the one in the URL.
  const [seenDomains, setSeenDomains] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!list.data) return;
    setSeenDomains((prev) => ({ ...prev, ...Object.fromEntries(list.data!.items.map((i) => [i.domain, i.domain_name])) }));
  }, [list.data]);
  const domainNames: Record<string, string> = {
    ...seenDomains,
    ...Object.fromEntries((rules.data?.items ?? []).map((r) => [r.domain, r.domain_name])),
  };
  if (domain && !(domain in domainNames)) domainNames[domain] = domain;
  const domainIds = Object.keys(domainNames).sort((a, b) => domainNames[a].localeCompare(domainNames[b]));

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  }

  const filtered = Boolean(severity || rule || status || domain);

  return (
    <>
      <h1>Findings</h1>
      <form className="filters" onSubmit={(e) => e.preventDefault()} aria-label="Filter findings">
        <div className="field">
          <label htmlFor="f-severity">Severity</label>
          <select id="f-severity" value={severity} onChange={(e) => setFilter('severity', e.target.value)}>
            <option value="">All</option>
            {SEVERITY_ORDER.map((s) => (
              <option key={s} value={s}>
                {SEVERITY_LABEL[s]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="f-rule">Rule</label>
          <select id="f-rule" value={rule} onChange={(e) => setFilter('rule', e.target.value)}>
            <option value="">All</option>
            {ruleIds.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="f-domain">Domain</label>
          <select id="f-domain" value={domain} onChange={(e) => setFilter('domain', e.target.value)}>
            <option value="">All</option>
            {domainIds.map((d) => (
              <option key={d} value={d}>
                {domainNames[d]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="f-status">Status</label>
          <select id="f-status" value={status} onChange={(e) => setFilter('status', e.target.value)}>
            <option value="">All</option>
            {STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </div>
        {filtered ? (
          <button type="button" onClick={() => setParams(new URLSearchParams())}>
            Clear filters
          </button>
        ) : null}
      </form>

      {list.error ? (
        <ErrorBox error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <Loading what="findings" />
      ) : list.data.items.length === 0 ? (
        <Empty>{filtered ? 'No findings match these filters.' : 'No findings for this period.'}</Empty>
      ) : (
        <>
          <p className="meta" role="status">
            {list.data.total} {list.data.total === 1 ? 'finding' : 'findings'}
            {filtered ? ' match the filters' : ''}
            {list.loading ? ' · Updating…' : ''}
          </p>
          <TableWrap label="Findings queue">
            <table className="clickable">
              <caption className="visually-hidden">Findings, in the order returned by the API (severity, rule, then exposure)</caption>
              <thead>
                <tr>
                  <th scope="col">Finding</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Headline</th>
                  <th scope="col">Rule</th>
                  <th scope="col">Domain</th>
                  <th scope="col" className="num">
                    Exposure
                  </th>
                  <th scope="col" className="num">
                    Employees affected
                  </th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((f) => (
                  <tr key={f.finding_id} data-severity={f.severity} onClick={() => navigate(`/findings/${encodeURIComponent(f.finding_id)}`)}>
                    <th scope="row">
                      <Link to={`/findings/${encodeURIComponent(f.finding_id)}`}>{f.finding_id}</Link>
                    </th>
                    <td>
                      <SeverityBadge severity={f.severity} />
                    </td>
                    <td className="headline">{f.headline}</td>
                    <td className="nowrap">{f.rule_id}</td>
                    <td className="nowrap">{f.domain_name}</td>
                    <td className="num">{formatUsd(f.exposure_usd)}</td>
                    <td className="num">{f.employee_count}</td>
                    <td>
                      <StatusBadge status={f.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </>
      )}
    </>
  );
}
