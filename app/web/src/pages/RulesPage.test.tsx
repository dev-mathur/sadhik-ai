import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFakeApi, samples } from '../test/fakeApi';
import { renderApp } from '../test/renderApp';

describe('rules and config', () => {
  it('shows the catalog with parameters (default, effective, direction, min, max, floor) and authorities', async () => {
    installFakeApi();
    renderApp('/rules');
    const card = (await screen.findByRole('heading', { name: /L-09/ })).closest('article')!;
    expect(card).toHaveTextContent('Contract'); // basis
    expect(card).toHaveTextContent('Unreviewed'); // review status
    expect(card).toHaveTextContent('Exception'); // last result
    expect(card).toHaveTextContent('FAR 31.201-4');
    const tbl = within(card).getByRole('table', { name: /parameters of L-09/i });
    const row = within(tbl).getAllByRole('row')[1];
    expect(row).toHaveTextContent('out_of_pop_hours_tolerance');
    expect(row).toHaveTextContent('Lower is stricter');
    const l05 = screen.getByRole('heading', { name: /L-05/ }).closest('article')!;
    const r = within(l05).getAllByRole('row')[1];
    expect(r).toHaveTextContent('72'); // default
    expect(r).toHaveTextContent('48'); // effective
    expect(r).toHaveTextContent('24'); // min
    expect(r).toHaveTextContent('168'); // max
    const l11 = screen.getByRole('heading', { name: /L-11/ }).closest('article')!;
    expect(l11).toHaveTextContent('Exception');
    expect(l11).toHaveTextContent('rate_data'); // required sources
    expect(l11).toHaveTextContent('CAS 418 (to verify)');
  });

  it('groups the catalog by domain with a heading per domain, and shows each rule\'s domain', async () => {
    installFakeApi();
    renderApp('/rules');
    await screen.findByRole('heading', { name: /L-09/ });
    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
    expect(headings.slice(0, 2)).toEqual(['Labor', 'DCAA cost accounting']);

    const labor = screen.getByRole('heading', { level: 3, name: 'Labor' }).closest('section')!;
    const dcaa = screen.getByRole('heading', { level: 3, name: 'DCAA cost accounting' }).closest('section')!;
    const ids = (sec: HTMLElement) => within(sec).getAllByRole('heading', { level: 4 }).map((h) => /^(DQ-\d+|[LC]-\d+)/.exec(h.textContent ?? '')![1]);
    expect(ids(labor)).toEqual(['L-05', 'L-09', 'DQ-01']);
    expect(ids(dcaa)).toEqual(['C-01', 'C-02', 'C-03', 'L-08', 'L-11']);

    // every card names its own domain
    for (const card of within(dcaa).getAllByRole('article')) expect(within(card).getByText('Domain').closest('div')).toHaveTextContent('DCAA cost accounting');
    for (const card of within(labor).getAllByRole('article')) expect(within(card).getByText('Domain').closest('div')).toHaveTextContent('Labor');
  });

  it('puts an unknown domain after the known ones, using the name the API sent', async () => {
    const items = [
      { ...samples.rulesList.items[0], id: 'X-01', domain: 'future_domain', domain_name: 'Future domain' },
      ...samples.rulesList.items,
    ];
    installFakeApi([(c) => (c.path === '/api/rules' ? { body: { ...samples.rulesList, items } } : undefined)]);
    renderApp('/rules');
    await screen.findByRole('heading', { name: /X-01/ });
    expect(screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent).slice(0, 3)).toEqual(['Labor', 'DCAA cost accounting', 'Future domain']);
  });

  it('shows an empty state when the catalog is empty', async () => {
    installFakeApi([(c) => (c.path === '/api/rules' ? { body: { ...samples.rulesList, items: [] } } : undefined)]);
    renderApp('/rules');
    expect(await screen.findByText('The rule catalog is empty.')).toBeInTheDocument();
  });

  it('shows the active YAML and the config version history', async () => {
    installFakeApi();
    renderApp('/rules');
    const ta = (await screen.findByLabelText(/Active rules configuration/)) as HTMLTextAreaElement;
    expect(ta.value).toBe(samples.configGet.yaml);
    const hist = await screen.findByRole('table', { name: /configuration versions/i });
    const rows = within(hist).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent('7');
    expect(rows[0]).toHaveTextContent('Active');
    expect(rows[0]).toHaveTextContent('r.delgado');
    expect(rows[0]).toHaveTextContent('Enabled the DCAA cost accounting domain');
    expect(rows[2]).toHaveTextContent('Not approved');
  });

  it('shows the domains line of the active configuration in the editor', async () => {
    installFakeApi();
    renderApp('/rules');
    const ta = (await screen.findByLabelText(/Active rules configuration/)) as HTMLTextAreaElement;
    expect(ta.value).toContain('domains: [labor, dcaa_cost_accounting]');
  });

  it('displays an unknown_domain error like any other code, with its line', async () => {
    const user = userEvent.setup();
    const rejected = {
      accepted: false,
      errors: [{ line: 8, path: 'domains[1]', code: 'unknown_domain', message: "domain 'payroll_audit' is not a known domain" }],
      warnings: [],
      sha256: 'ab12cd34ef56',
      config_version: null,
    };
    installFakeApi([(c) => (c.path === '/api/config/validate' ? { status: 200, body: rejected } : undefined)]);
    renderApp('/rules');
    fireEvent.change(await screen.findByLabelText(/Active rules configuration/), { target: { value: 'domains: [labor, payroll_audit]\n' } });
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    const errors = await screen.findByRole('table', { name: /errors found in the configuration/i });
    const row = within(errors).getAllByRole('row')[1];
    expect(within(row).getByRole('rowheader')).toHaveTextContent('8');
    expect(row).toHaveTextContent('unknown_domain');
    expect(row).toHaveTextContent('domains[1]');
    expect(row).toHaveTextContent("domain 'payroll_audit' is not a known domain");
    expect(screen.getByRole('alert')).toHaveTextContent(/Rejected/);
  });

  it('copes with an error that cannot be attributed to a line (line is null)', async () => {
    const user = userEvent.setup();
    const rejected = {
      accepted: false,
      errors: [{ line: null, path: '', code: 'yaml_syntax', message: 'mapping values are not allowed here' }],
      warnings: [],
      sha256: 'ab12cd34ef56',
      config_version: null,
    };
    installFakeApi([(c) => (c.path === '/api/config/validate' ? { status: 200, body: rejected } : undefined)]);
    renderApp('/rules');
    fireEvent.change(await screen.findByLabelText(/Active rules configuration/), { target: { value: ': :' } });
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    const errors = await screen.findByRole('table', { name: /errors found in the configuration/i });
    const row = within(errors).getAllByRole('row')[1];
    expect(within(row).getByRole('rowheader')).toHaveTextContent('No line');
    expect(within(row).queryByRole('button')).toBeNull();
    expect(row).toHaveTextContent('yaml_syntax');
  });

  it('shows per-error line numbers and codes from a rejected config', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi();
    renderApp('/rules');
    const ta = await screen.findByLabelText(/Active rules configuration/);
    fireEvent.change(ta, { target: { value: 'config_version: 7\nREJECT_ME: true\n' } });
    await user.click(screen.getByRole('button', { name: 'Validate' }));

    const errors = await screen.findByRole('table', { name: /errors found in the configuration/i });
    const rows = within(errors).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(samples.configRejected.errors.length);
    samples.configRejected.errors.forEach((e, i) => {
      expect(within(rows[i]).getByRole('rowheader')).toHaveTextContent(String(e.line));
      expect(rows[i]).toHaveTextContent(e.code);
      expect(rows[i]).toHaveTextContent(e.path);
      expect(rows[i]).toHaveTextContent(e.message);
    });
    expect(screen.getByRole('alert')).toHaveTextContent(/Rejected/);
    expect(calls.find((c) => c.path === '/api/config/validate')!.body).toEqual({ yaml: 'config_version: 7\nREJECT_ME: true\n' });
    // nothing was saved
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  });

  it('shows the errors when Save is rejected (422) and does not reload the active version', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi();
    renderApp('/rules');
    fireEvent.change(await screen.findByLabelText(/Active rules configuration/), { target: { value: 'REJECT_ME' } });
    await user.click(screen.getByRole('button', { name: 'Save' }));
    const errors = await screen.findByRole('table', { name: /errors found in the configuration/i });
    expect(errors).toHaveTextContent('exceeds_max');
    expect(errors).toHaveTextContent('looser_than_floor');
    expect(errors).toHaveTextContent('floor_not_settable');
    expect(errors).toHaveTextContent('cannot_disable_regulatory_rule');
    expect(calls.filter((c) => c.method === 'GET' && c.path === '/api/config')).toHaveLength(1);
  });

  it('shows warnings and the new version for an accepted config, and reloads after Save', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi();
    renderApp('/rules');
    fireEvent.change(await screen.findByLabelText(/Active rules configuration/), { target: { value: 'config_version: 7\n' } });
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    await screen.findByText(/Accepted \(not saved\)/);
    expect(screen.getByText(/late_threshold_hours is stricter than the default/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === 'PUT')).toBe(false);

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText(/Accepted and saved/);
    expect(screen.getByText(/version 8/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.method === 'GET' && c.path === '/api/config')).toHaveLength(2));
  });

  it('shows the API error code and message for the catalog', async () => {
    installFakeApi([(c) => (c.path === '/api/rules' ? { status: 500, body: { error: { code: 'catalog_error', message: 'Catalog unavailable' } } } : undefined)]);
    renderApp('/rules');
    const alerts = await screen.findAllByRole('alert');
    expect(alerts[0]).toHaveTextContent('catalog_error');
    expect(alerts[0]).toHaveTextContent('Catalog unavailable');
  });
});
