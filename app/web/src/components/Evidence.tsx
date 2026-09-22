import { useState } from 'react';
import { api } from '../api/client';
import { useAsync } from '../hooks';
import { Empty, ErrorBox, Loading, TableWrap } from './States';
import { humanize } from '../lib/format';

export function Evidence({ findingId }: { findingId: string }) {
  const [page, setPage] = useState(1);
  const { data, error, loading, reload } = useAsync(() => api.evidence(findingId, page), [findingId, page]);

  return (
    <section aria-labelledby="sec-evidence" id="evidence">
      <h2 id="sec-evidence">Evidence</h2>
      {error ? (
        <ErrorBox error={error} onRetry={reload} />
      ) : !data ? (
        <Loading what="evidence" />
      ) : data.items.length === 0 ? (
        <Empty>No evidence records were returned for this finding.</Empty>
      ) : (
        <>
          <p className="meta">
            {data.total} evidence {data.total === 1 ? 'record' : 'records'} {'·'} page {data.page}
            {loading ? ' · Updating…' : ''}
          </p>
          <TableWrap label="Evidence records">
            <table>
              <caption className="visually-hidden">Source records behind this finding</caption>
              <thead>
                <tr>
                  <th scope="col">Kind</th>
                  <th scope="col">Reference</th>
                  <th scope="col">Source file : row</th>
                  <th scope="col">Key details</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((e, i) => (
                  <tr key={`${e.kind}-${e.ref_id}-${i}`}>
                    <td className="nowrap">{humanize(e.kind)}</td>
                    <th scope="row">{e.ref_id}</th>
                    <td>
                      <code>
                        {e.source_file}
                        {e.row !== null && e.row !== undefined ? ` : ${e.row}` : ''}
                      </code>
                      <br />
                      <span className="muted small hash">sha256 {e.sha256}</span>
                    </td>
                    <td>
                      <dl className="detail-list">
                        {Object.entries(e.detail).map(([k, v]) => (
                          <div key={k}>
                            <dt>{k}</dt>
                            <dd>{v === null ? '—' : String(v)}</dd>
                          </div>
                        ))}
                      </dl>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <div className="button-row" role="group" aria-label="Evidence pages">
            <button type="button" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)}>
              Previous page
            </button>
            <button type="button" disabled={loading || page * data.page_size >= data.total} onClick={() => setPage(page + 1)}>
              Next page
            </button>
          </div>
        </>
      )}
    </section>
  );
}
