import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFakeApi, samples } from '../test/fakeApi';
import { renderApp } from '../test/renderApp';

describe('findings queue', () => {
  it('lists findings in the order the API returned them, each with severity text, headline, exposure, employees, status', async () => {
    installFakeApi();
    renderApp('/findings');
    const table = await screen.findByRole('table', { name: /findings, in the order returned/i });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(samples.findingsList.items.length);
    expect(within(rows[0]).getByRole('rowheader')).toHaveTextContent('F-3311');
    expect(within(rows[7]).getByRole('rowheader')).toHaveTextContent('F-3318');
    expect(within(rows[2]).getByRole('rowheader')).toHaveTextContent('F-3313');
    expect(rows[2]).toHaveTextContent('DCAA cost accounting');
    expect(rows[0]).toHaveTextContent('Labor');
    expect(rows[0]).toHaveTextContent('High');
    expect(rows[0]).toHaveTextContent('Data Engineer III billed on C-8841');
    expect(rows[0]).toHaveTextContent('$3,504.00');
    expect(rows[0]).toHaveTextContent('Open');
    expect(rows[1]).toHaveTextContent('$27,518.40');
    expect(rows[1]).toHaveTextContent('9'); // employees affected
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Finding',
      'Severity',
      'Headline',
      'Rule',
      'Domain',
      'Exposure',
      'Employees affected',
      'Status',
    ]);
    expect(within(rows[0]).getByRole('link', { name: 'F-3311' })).toHaveAttribute('href', '/findings/F-3311');
  });

  it('passes the chosen filters to the API', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi();
    renderApp('/findings');
    await screen.findByRole('table');
    await user.selectOptions(screen.getByLabelText('Severity'), 'high');
    await user.selectOptions(screen.getByLabelText('Status'), 'in_review');
    await screen.findByRole('button', { name: 'Clear filters' });
    const last = calls.filter((c) => c.path === '/api/findings').pop()!;
    const q = new URLSearchParams(last.search);
    expect(q.get('severity')).toBe('high');
    expect(q.get('status')).toBe('in_review');
    // rule choices come from the catalog and from findings seen (DQ-01 is in the queue but not the sample catalog)
    const rule = screen.getByLabelText('Rule');
    expect(within(rule).getByRole('option', { name: 'L-05' })).toBeInTheDocument();
    expect(within(rule).getByRole('option', { name: 'DQ-01' })).toBeInTheDocument();
  });

  it('has a Domain filter (All / Labor / DCAA cost accounting) that narrows the rows and updates the URL', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi();
    renderApp('/findings');
    const table = await screen.findByRole('table', { name: /findings, in the order returned/i });
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(14);

    const domain = screen.getByLabelText('Domain');
    await waitFor(() => expect(within(domain).getAllByRole('option').map((o) => o.textContent)).toEqual(['All', 'DCAA cost accounting', 'Labor']));

    await user.selectOptions(domain, 'DCAA cost accounting');
    await waitFor(() => expect(within(screen.getByRole('table')).getAllByRole('row').slice(1)).toHaveLength(6));
    expect(screen.getByTestId('location')).toHaveTextContent('/findings?domain=dcaa_cost_accounting');
    const ids = within(screen.getByRole('table')).getAllByRole('row').slice(1).map((r) => within(r).getByRole('rowheader').textContent);
    expect(ids).toEqual(['F-3313', 'F-3315', 'F-3321', 'F-3322', 'F-3323', 'F-3324']);
    for (const r of within(screen.getByRole('table')).getAllByRole('row').slice(1)) expect(r).toHaveTextContent('DCAA cost accounting');
    expect(new URLSearchParams(calls.filter((c) => c.path === '/api/findings').pop()!.search).get('domain')).toBe('dcaa_cost_accounting');
    expect(screen.getByRole('status')).toHaveTextContent('6 findings match the filters');

    // the domain filter composes with the others and is cleared by Clear filters
    await user.selectOptions(screen.getByLabelText('Severity'), 'high');
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('domain=dcaa_cost_accounting'));
    expect(screen.getByTestId('location')).toHaveTextContent('severity=high');
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() => expect(within(screen.getByRole('table')).getAllByRole('row').slice(1)).toHaveLength(14));
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/findings$/);

    await user.selectOptions(screen.getByLabelText('Domain'), 'Labor');
    await waitFor(() => expect(within(screen.getByRole('table')).getAllByRole('row').slice(1)).toHaveLength(8));
    expect(screen.getByTestId('location')).toHaveTextContent('/findings?domain=labor');
  });

  it('reads the domain from the query string on load and shows it selected', async () => {
    const { calls } = installFakeApi();
    renderApp('/findings?domain=labor');
    const table = await screen.findByRole('table', { name: /findings, in the order returned/i });
    expect(within(table).getAllByRole('row').slice(1)).toHaveLength(8);
    expect(screen.getByLabelText('Domain')).toHaveValue('labor');
    expect(new URLSearchParams(calls.find((c) => c.path === '/api/findings')!.search).get('domain')).toBe('labor');
    expect(screen.getByRole('button', { name: 'Clear filters' })).toBeInTheDocument();
  });

  it('shows the API error for an unknown domain and keeps the filters usable', async () => {
    installFakeApi([(c) => (c.path === '/api/findings' && c.search.includes('domain=nope') ? { status: 400, body: { error: { code: 'bad_filter', message: 'Unknown domain: nope' } } } : undefined)]);
    renderApp('/findings?domain=nope');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('bad_filter');
    expect(screen.getByLabelText('Domain')).toBeInTheDocument();
  });

  it('shows an empty state when nothing matches', async () => {
    installFakeApi([(c) => (c.path === '/api/findings' ? { body: { total: 0, items: [] } } : undefined)]);
    renderApp('/findings?severity=low');
    expect(await screen.findByText('No findings match these filters.')).toBeInTheDocument();
  });

  it('shows the API error code and message', async () => {
    installFakeApi([(c) => (c.path === '/api/findings' ? { status: 400, body: { error: { code: 'bad_filter', message: 'Unknown severity' } } } : undefined)]);
    renderApp('/findings');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('bad_filter');
    expect(alert).toHaveTextContent('Unknown severity');
  });

  it('shows a loading state before the data arrives', async () => {
    installFakeApi();
    renderApp('/findings');
    expect(screen.getByRole('status')).toHaveTextContent(/Loading findings/);
    await screen.findByRole('table');
  });
});
