import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFakeApi, samples } from '../test/fakeApi';
import { renderApp } from '../test/renderApp';

describe('runs', () => {
  it('lists runs and shows the manifest and per-rule results for the selected run', async () => {
    const user = userEvent.setup();
    installFakeApi();
    renderApp('/runs');
    const list = await screen.findByRole('table', { name: /runs, as returned/i });
    expect(list).toHaveTextContent('RUN-2026-08-MER-001');
    expect(list).toHaveTextContent('$102,491.51');
    await user.click(within(list).getByRole('button', { name: 'RUN-2026-08-MER-001' }));

    const inputs = await screen.findByRole('table', { name: /input files pinned/i });
    for (const i of samples.runDetail.manifest.inputs) {
      expect(inputs).toHaveTextContent(i.file);
      expect(inputs).toHaveTextContent(i.sha256);
    }
    expect(screen.getByText(/Sources not provided:/).closest('p')).toHaveTextContent('none');
    expect(screen.getByText('Materiality').closest('div')).toHaveTextContent('$9,931.20');
    expect(screen.getByText('Configuration').closest('div')).toHaveTextContent('version 7');
    const rules = screen.getByRole('table', { name: /result of each rule/i });
    expect(within(rules).getAllByRole('row')).toHaveLength(samples.runDetail.rules.length + 1);
    expect(rules).toHaveTextContent('M3');
    expect(rules).toHaveTextContent('M10');
  });

  it('shows the enabled domains, rate_data among the inputs and the reference tables', async () => {
    installFakeApi();
    renderApp('/runs?run=RUN-2026-08-MER-001');
    const inputs = await screen.findByRole('table', { name: /input files pinned/i });
    const rate = within(inputs).getByRole('rowheader', { name: 'rate_data' }).closest('tr')!;
    expect(rate).toHaveTextContent('meridian_rates_2026-08.csv');
    expect(rate).toHaveTextContent('3e5b9d10a7c4');
    expect(rate).toHaveTextContent('2026-08-31');

    const domains = screen.getByText('Enabled domains').closest('div')!;
    expect(domains).toHaveTextContent('labor');
    expect(domains).toHaveTextContent('dcaa_cost_accounting');

    const tables = screen.getByRole('table', { name: /reference tables pinned/i });
    const rows = within(tables).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(4); // account_categories, classification_history, account_allowability, ics_submissions
    expect(rows[0]).toHaveTextContent('account_categories');
    expect(rows[0]).toHaveTextContent('a91f2c66d0e1');
    expect(rows[1]).toHaveTextContent('classification_history');
    expect(rows[1]).toHaveTextContent('77d3e8b4c2a9');

    // each rule row carries its domain
    const rules = screen.getByRole('table', { name: /result of each rule/i });
    const l11 = within(rules).getByRole('rowheader', { name: 'L-11' }).closest('tr')!;
    expect(l11).toHaveTextContent('dcaa_cost_accounting');
  });

  it('copes with a run that pinned no reference tables and no enabled-domain record', async () => {
    const detail = { ...samples.runDetail, manifest: { ...samples.runDetail.manifest, enabled_domains: [], reference_tables: [] } };
    installFakeApi([(c) => (c.method === 'GET' && c.path === '/api/runs/RUN-2026-08-MER-001' ? { body: detail } : undefined)]);
    renderApp('/runs?run=RUN-2026-08-MER-001');
    expect(await screen.findByText('This run pinned no reference tables.')).toBeInTheDocument();
    expect(screen.getByText('Enabled domains').closest('div')).toHaveTextContent('none recorded');
  });

  it('replays a run and reports identical true', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi();
    renderApp('/runs?run=RUN-2026-08-MER-001');
    await user.click(await screen.findByRole('button', { name: 'Replay this run' }));
    const res = await screen.findByText(/Identical: true/);
    expect(res.closest('[role=status]')).toHaveTextContent('14 findings compared');
    expect(res.closest('[role=status]')).toHaveTextContent('No differences');
    expect(calls.some((c) => c.method === 'POST' && c.path === '/api/runs/RUN-2026-08-MER-001/replay')).toBe(true);
  });

  it('shows the diff when a replay is not identical', async () => {
    const user = userEvent.setup();
    installFakeApi([
      (c) =>
        c.method === 'POST' && c.path.endsWith('/replay')
          ? { body: { run_id: 'RUN-2026-08-MER-001', identical: false, findings_compared: 8, diff: [{ finding_id: 'F-3312', field: 'exposure_usd' }], reason: 'Config changed' } }
          : undefined,
    ]);
    renderApp('/runs?run=RUN-2026-08-MER-001');
    await user.click(await screen.findByRole('button', { name: 'Replay this run' }));
    expect(await screen.findByText(/Identical: false/)).toBeInTheDocument();
    expect(screen.getByText(/Config changed/)).toBeInTheDocument();
    expect(screen.getByText(/F-3312/, { selector: 'code' })).toBeInTheDocument();
  });

  it('shows an empty state and an error state', async () => {
    installFakeApi([(c) => (c.path === '/api/runs' ? { body: { items: [] } } : undefined)]);
    const { unmount } = renderApp('/runs');
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
    unmount();
    installFakeApi([(c) => (c.path === '/api/runs' ? { status: 500, body: { error: { code: 'runs_failed', message: 'Cannot list runs' } } } : undefined)]);
    renderApp('/runs');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('runs_failed');
    expect(alert).toHaveTextContent('Cannot list runs');
  });
});
