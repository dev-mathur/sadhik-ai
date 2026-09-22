import { api } from '../api/client';
import type { Rule } from '../api/types';
import { ResultBadge } from '../components/Badges';
import { ConfigEditor } from '../components/ConfigEditor';
import { Glyph } from '../components/Glyph';
import { Icons } from '../lib/icons';
import { Empty, ErrorBox, Loading, TableWrap } from '../components/States';
import { useAsync, useTitle } from '../hooks';
import { dash, humanize } from '../lib/format';

// Known domains keep a fixed order; anything else follows in the order the API returned it.
const DOMAIN_ORDER = ['labor', 'dcaa_cost_accounting'];

function groupByDomain(rules: Rule[]): { domain: string; name: string; rules: Rule[] }[] {
  const groups = new Map<string, { domain: string; name: string; rules: Rule[] }>();
  for (const r of rules) {
    const g = groups.get(r.domain) ?? { domain: r.domain, name: r.domain_name, rules: [] };
    g.rules.push(r);
    groups.set(r.domain, g);
  }
  const rank = (d: string) => (DOMAIN_ORDER.includes(d) ? DOMAIN_ORDER.indexOf(d) : DOMAIN_ORDER.length);
  return Array.from(groups.values()).sort((a, b) => rank(a.domain) - rank(b.domain));
}

export function RulesPage() {
  useTitle('Rules & config');
  const rules = useAsync(() => api.rules(), []);
  const config = useAsync(() => api.config(), []);

  return (
    <>
      <h1>Rules &amp; config</h1>

      <section aria-labelledby="catalog-heading">
        <h2 id="catalog-heading">Rule catalog</h2>
        {rules.error ? (
          <ErrorBox error={rules.error} onRetry={rules.reload} />
        ) : !rules.data ? (
          <Loading what="rules" />
        ) : rules.data.items.length === 0 ? (
          <Empty>The rule catalog is empty.</Empty>
        ) : (
          <>
            <p className="meta">Floor registry version {rules.data.floor_registry_version}. Floors are owned by Sadhik and cannot be loosened by a customer configuration.</p>
            {groupByDomain(rules.data.items).map((g) => (
              <section key={g.domain} aria-labelledby={`domain-${g.domain}`} className="rule-domain">
                <h3 id={`domain-${g.domain}`}>{g.name}</h3>
                <ul className="rule-list">
                  {g.rules.map((r) => (
                    <li key={r.id}>
                      <RuleCard rule={r} />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </>
        )}
      </section>

      <section aria-labelledby="config-heading">
        <h2 id="config-heading">Configuration</h2>
        {config.error ? (
          <ErrorBox error={config.error} onRetry={config.reload} />
        ) : !config.data ? (
          <Loading what="configuration" />
        ) : (
          <>
            <ConfigEditor config={config.data} onSaved={config.reload} />
            <h3 id="config-history-heading">Version history</h3>
            {config.data.history.length === 0 ? (
              <Empty>No earlier versions.</Empty>
            ) : (
              <TableWrap label="Configuration version history">
                <table>
                  <caption className="visually-hidden">Configuration versions, newest first</caption>
                  <thead>
                    <tr>
                      <th scope="col">Version</th>
                      <th scope="col">Changed by</th>
                      <th scope="col">Approved by</th>
                      <th scope="col">Reason</th>
                      <th scope="col">Effective from</th>
                      <th scope="col">sha256</th>
                    </tr>
                  </thead>
                  <tbody>
                    {config.data.history.map((h) => (
                      <tr key={h.config_version}>
                        <th scope="row">
                          {h.config_version}
                          {h.config_version === config.data!.config_version ? <span className="badge tier-ok"><Glyph icon={Icons.CheckCircle} /> Active</span> : null}
                        </th>
                        <td>{h.changed_by}</td>
                        <td>{h.approved_by ?? 'Not approved'}</td>
                        <td>{h.reason}</td>
                        <td>{h.effective_from}</td>
                        <td>
                          <code>{h.sha256}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </>
        )}
      </section>
    </>
  );
}

function RuleCard({ rule: r }: { rule: Rule }) {
  return (
    <article className="card rule-card" aria-labelledby={`rule-${r.id}`}>
      <h4 id={`rule-${r.id}`}>
        {r.id} <span className="muted">v{r.version}</span> {'—'} {r.title}
      </h4>
      <dl className="facts compact">
        <div>
          <dt>Domain</dt>
          <dd>{r.domain_name}</dd>
        </div>
        <div>
          <dt>Tier</dt>
          <dd>{humanize(r.tier)}</dd>
        </div>
        <div>
          <dt>Basis</dt>
          <dd>{humanize(r.basis)}</dd>
        </div>
        <div>
          <dt>Review status</dt>
          <dd>{humanize(r.review_status)}</dd>
        </div>
        <div>
          <dt>Rule status</dt>
          <dd>{humanize(r.status)}</dd>
        </div>
        <div>
          <dt>Last result</dt>
          <dd>
            <ResultBadge result={r.last_result} />
          </dd>
        </div>
        <div>
          <dt>Required sources</dt>
          <dd>{r.required_sources.join(', ') || '—'}</dd>
        </div>
      </dl>
      <h5>Authorities</h5>
      <ul>
        {r.authorities.map((a) => (
          <li key={a}>{a}</li>
        ))}
      </ul>
      <h5>Parameters</h5>
      {r.parameters.length === 0 ? (
        <p className="muted">This rule has no adjustable parameters.</p>
      ) : (
        <TableWrap label={`Parameters of ${r.id}`}>
          <table>
            <caption className="visually-hidden">Parameters of {r.id}</caption>
            <thead>
              <tr>
                <th scope="col">Parameter</th>
                <th scope="col">Default</th>
                <th scope="col">Effective</th>
                <th scope="col">Direction</th>
                <th scope="col">Min</th>
                <th scope="col">Max</th>
                <th scope="col">Floor</th>
                <th scope="col">Unit</th>
              </tr>
            </thead>
            <tbody>
              {r.parameters.map((p) => (
                <tr key={p.name}>
                  <th scope="row">
                    <code>{p.name}</code>
                    {p.description ? <div className="muted small">{p.description}</div> : null}
                  </th>
                  <td>{p.default}</td>
                  <td>
                    <strong>{p.effective}</strong>
                  </td>
                  <td>{humanize(p.direction)}</td>
                  <td>{dash(p.min)}</td>
                  <td>{dash(p.max)}</td>
                  <td>{dash(p.floor)}</td>
                  <td>{dash(p.unit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </article>
  );
}
