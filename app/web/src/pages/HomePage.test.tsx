import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFakeApi, samples } from '../test/fakeApi';
import { renderApp } from '../test/renderApp';

const domainTile = (name: RegExp | string) => screen.getByRole('link', { name }).closest('li')!;
const tileByHeading = (name: string) => screen.getByText(name, { selector: '.domain-name' }).closest('li')!;

describe('status page', () => {
  it('shows the one overall label, severity counts, exposure and coverage across the enabled domains', async () => {
    installFakeApi();
    renderApp('/');
    expect(await screen.findByText('14 open findings')).toBeInTheDocument();
    expect(screen.getByText('$102,491.51')).toBeInTheDocument();
    expect(screen.getByText('11 of 11 rules evaluated')).toBeInTheDocument();
    expect(screen.getByText(/Across enabled domains: Labor, DCAA cost accounting/)).toBeInTheDocument();
    // severity is both a glyph-plus-text badge and a count
    const sev = screen.getByText('Open findings by severity').closest('div')!;
    expect(within(sev).getByText('High')).toBeInTheDocument();
    expect(within(sev).getByText('8')).toBeInTheDocument(); // high
    expect(within(sev).getByText('5')).toBeInTheDocument(); // medium
    expect(within(sev).getByText('1')).toBeInTheDocument();
  });

  it('renders each enabled domain as a tile with its own state, counts and coverage, linking to the filtered queue', async () => {
    installFakeApi();
    renderApp('/');
    await screen.findByText('14 open findings');

    const labor = screen.getByRole('link', { name: /Labor.*8 open findings/ });
    expect(labor).toHaveAttribute('href', '/findings?domain=labor');
    const laborTile = domainTile(/Labor.*8 open findings/);
    expect(laborTile).toHaveTextContent('6 of 6 rules evaluated');
    const laborOpen = within(laborTile).getByText('Open findings').closest('div')!;
    expect(laborOpen).toHaveTextContent('8');
    const laborSev = within(laborTile).getByText('By severity').closest('div')!;
    expect(laborSev).toHaveTextContent('High');
    expect(laborSev).toHaveTextContent('3');
    expect(laborSev).toHaveTextContent('Medium');
    expect(laborSev).toHaveTextContent('4');
    expect(laborSev).toHaveTextContent('Low');
    expect(laborSev).toHaveTextContent('1');

    const dcaa = screen.getByRole('link', { name: /DCAA cost accounting.*6 open findings/ });
    expect(dcaa).toHaveAttribute('href', '/findings?domain=dcaa_cost_accounting');
    const dcaaTile = domainTile(/DCAA cost accounting.*6 open findings/);
    expect(dcaaTile).toHaveTextContent('5 of 5 rules evaluated');
    expect(within(dcaaTile).getByText('Open findings').closest('div')).toHaveTextContent('6');
    const dcaaSev = within(dcaaTile).getByText('By severity').closest('div')!;
    expect(dcaaSev).toHaveTextContent(/High\s*5/);
    expect(dcaaSev).toHaveTextContent(/Medium\s*1/);
    expect(dcaaSev).toHaveTextContent(/Low\s*0/);
  });

  it('follows a domain tile to the queue filtered to that domain', async () => {
    const user = userEvent.setup();
    installFakeApi();
    renderApp('/');
    await user.click(await screen.findByRole('link', { name: /DCAA cost accounting.*6 open findings/ }));
    expect(screen.getByTestId('location')).toHaveTextContent('/findings?domain=dcaa_cost_accounting');
    const table = await screen.findByRole('table', { name: /findings, in the order returned/i });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getByRole('rowheader').textContent)).toEqual(['F-3313', 'F-3315', 'F-3321', 'F-3322', 'F-3323', 'F-3324']);
    expect(screen.getByLabelText('Domain')).toHaveValue('dcaa_cost_accounting');
  });

  it('shows placeholder domains as "Coming later" and neither they nor a disabled domain are links', async () => {
    installFakeApi();
    renderApp('/');
    await screen.findByText('14 open findings');
    for (const name of ['CMMC evidence', 'Proposals']) {
      const tile = tileByHeading(name);
      expect(tile).toHaveTextContent('Coming later');
      expect(within(tile).queryByRole('link')).toBeNull();
    }
    expect(screen.getAllByText(/Coming later/i)).toHaveLength(2);
    expect(screen.getAllByRole('link', { name: /open findings/ })).toHaveLength(2);
  });

  it('shows an available but disabled domain as "Not enabled", not a link, with no counts', async () => {
    const status = {
      ...samples.status,
      domains: samples.status.domains.map((d) =>
        d.id === 'dcaa_cost_accounting'
          ? { ...d, enabled: false, state: 'Not enabled', label_kind: 'not_enabled', open_findings: 0, by_severity: { high: 0, medium: 0, low: 0 }, coverage: null }
          : d,
      ),
    };
    installFakeApi([(c) => (c.path === '/api/status' ? { body: status } : undefined)]);
    renderApp('/');
    await screen.findByText('14 open findings');
    const tile = tileByHeading('DCAA cost accounting');
    expect(tile).toHaveTextContent('Not enabled');
    expect(within(tile).queryByRole('link')).toBeNull();
    expect(tile).not.toHaveTextContent('rules evaluated');
    // the enabled domain keeps its link
    expect(screen.getByRole('link', { name: /Labor.*8 open findings/ })).toBeInTheDocument();
  });

  it('conveys an incomplete-data domain in the link name, with a glyph and text', async () => {
    const status = {
      ...samples.status,
      domains: samples.status.domains.map((d) =>
        d.id === 'dcaa_cost_accounting'
          ? { ...d, state: 'Incomplete data', label_kind: 'incomplete_data', coverage: { evaluated: 1, applicable: 2, ratio: '0.5000', floor: '0.85' } }
          : d,
      ),
    };
    installFakeApi([(c) => (c.path === '/api/status' ? { body: status } : undefined)]);
    renderApp('/');
    await screen.findByText('14 open findings');
    const link = screen.getByRole('link', { name: /DCAA cost accounting.*Incomplete data/ });
    expect(link).toHaveAttribute('href', '/findings?domain=dcaa_cost_accounting');
    expect(link.closest('li')).toHaveTextContent('1 of 2 rules evaluated');
    expect(link.querySelector('.domain-state [aria-hidden="true"]')).not.toBeNull();
  });

  it('lists each rule result with its domain name', async () => {
    installFakeApi();
    renderApp('/');
    const results = (await screen.findByRole('heading', { name: 'Rule results' })).closest('section')!;
    const l11 = within(results).getByText('L-11').closest('li')!;
    expect(l11).toHaveTextContent('Exception');
    expect(l11).toHaveTextContent('DCAA cost accounting');
    expect(within(results).getByText('L-05').closest('li')).toHaveTextContent('Labor');
  });

  it('renders the per-tier block with all three tiers, and the oldest-source line', async () => {
    installFakeApi();
    renderApp('/');
    const table = await screen.findByRole('table', { name: /state of each run tier/i });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    const [fast, pay, close] = rows;
    expect(within(fast).getByRole('rowheader')).toHaveTextContent('Fast');
    expect(within(pay).getByRole('rowheader')).toHaveTextContent('Pay period');
    expect(within(close).getByRole('rowheader')).toHaveTextContent('Close');
    // cadence, rules evaluated of rules in tier (both straight from the payload), state (text, not colour alone), next due, note
    expect(fast).toHaveTextContent('Weekly');
    expect(fast).toHaveTextContent('4 of 4');
    expect(fast).toHaveTextContent('Overdue');
    expect(fast).toHaveTextContent('2026-09-10');
    expect(fast).toHaveTextContent('the next run was due 2026-09-10');
    expect(pay).toHaveTextContent('Semi monthly');
    expect(pay).toHaveTextContent('2 of 2');
    expect(pay).toHaveTextContent('Overdue');
    expect(close).toHaveTextContent('5 of 5'); // L-03, L-11 and the three C-rules
    expect(close).toHaveTextContent('Current');
    expect(close).toHaveTextContent('2026-10-03');

    const oldest = screen.getByText(/Oldest source:/).closest('p')!;
    expect(oldest).toHaveTextContent('hris');
    expect(oldest).toHaveTextContent('bamboo_roster_2026-08-01.csv');
    expect(oldest).toHaveTextContent('2026-08-01');
    expect(oldest).toHaveTextContent('50 days old');
  });

  it('runs now for the shown period and the chosen tier, then reports the result', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi();
    renderApp('/');
    await screen.findByText('14 open findings');
    await user.selectOptions(screen.getByLabelText('Tier'), 'pay_period');
    await user.click(screen.getByRole('button', { name: 'Run now' }));
    const done = await screen.findByText(/finished: 14 open findings/);
    expect(done).toBeInTheDocument();
    const post = calls.find((c) => c.method === 'POST' && c.path === '/api/runs')!;
    expect(post.body).toEqual({ period: '2026-08', tier: 'pay_period' });
  });

  it('shows the API error code and message', async () => {
    installFakeApi([(c) => (c.path === '/api/status' ? { status: 500, body: { error: { code: 'store_unavailable', message: 'The findings store is offline' } } } : undefined)]);
    renderApp('/');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('store_unavailable');
    expect(alert).toHaveTextContent('The findings store is offline');
  });

  it('renders the footer disclaimer verbatim', async () => {
    installFakeApi();
    renderApp('/');
    await screen.findByText('14 open findings');
    expect(screen.getByRole('contentinfo')).toHaveTextContent('This is an analysis of submitted data. It is not an audit opinion or an attestation.');
  });
});
