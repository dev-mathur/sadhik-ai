import fs from 'node:fs';
import path from 'node:path';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { installFakeApi, samples } from '../test/fakeApi';
import { renderApp } from '../test/renderApp';

beforeEach(() => {
  window.localStorage.clear();
});

const HEADINGS = ['What happened', 'Why it matters', 'Impact', 'Recommended action', 'Evidence', 'Disposition', 'History'];

describe('finding detail', () => {
  it('renders sections in the mandated order: what happened, why it matters, impact, action, evidence, disposition, history', async () => {
    installFakeApi();
    renderApp('/findings/F-3311');
    const els = [];
    for (const name of HEADINGS) els.push(await screen.findByRole('heading', { level: 2, name }));
    // the evidence table itself loads after the heading; wait for it so the whole page is in the DOM
    await screen.findByRole('table', { name: /source records behind this finding/i });

    for (let i = 0; i < els.length - 1; i++) {
      const follows = els[i].compareDocumentPosition(els[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING;
      expect(follows, `${HEADINGS[i]} must precede ${HEADINGS[i + 1]}`).toBeTruthy();
    }

    // explanation text sits above the evidence table
    const table = screen.getByRole('table', { name: /source records behind this finding/i });
    const what = screen.getByText(/recorded 96\.0 hours on contract C-8841/);
    expect(what.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // the disposition controls follow the evidence table
    const disp = screen.getByRole('heading', { level: 2, name: 'Disposition' });
    expect(table.compareDocumentPosition(disp) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('cites authorities under why it matters, shows formatted impact, and evidence as file:row', async () => {
    installFakeApi();
    renderApp('/findings/F-3311');
    const why = (await screen.findByRole('heading', { name: 'Why it matters' })).closest('section')!;
    expect(within(why).getByText('FAR 52.232-7')).toBeInTheDocument();
    expect(within(why).getByText('FAR 31.201-2')).toBeInTheDocument();
    const impact = screen.getByRole('heading', { name: 'Impact' }).closest('section')!;
    expect(within(impact).getAllByText(/\$3,504\.00/).length).toBeGreaterThan(0);
    await screen.findByText('meridian_time_2026-08.csv : 1188');
    expect(screen.getByText('TE-884112')).toBeInTheDocument();
    // history renders the recorded transition
    expect(screen.getByText(/Opened by RUN-2026-08-MER-001/)).toBeInTheDocument();
  });

  it('shows the domain name next to the rule', async () => {
    installFakeApi();
    renderApp('/findings/F-3311');
    await screen.findByRole('heading', { level: 1 });
    const meta = screen.getByText(/^Rule L-01/).closest('ul')!;
    expect(meta).toHaveTextContent('Rule L-01');
    expect(meta).toHaveTextContent('Domain Labor');
  });

  it('shows an empty-affected DCAA finding without crashing', async () => {
    installFakeApi();
    renderApp('/findings/F-3315');
    const impact = (await screen.findByRole('heading', { name: 'Impact' })).closest('section')!;
    expect(within(impact).getByText('Employees affected').closest('div')).toHaveTextContent('None');
    expect(within(impact).getByText('Contracts affected').closest('div')).toHaveTextContent('None');
  });

  it('shows the API error code and message when the finding cannot be loaded', async () => {
    installFakeApi([(c) => (c.path === '/api/findings/F-9999' ? { status: 404, body: { error: { code: 'finding_not_found', message: 'No finding F-9999' } } } : undefined)]);
    renderApp('/findings/F-9999');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('finding_not_found');
    expect(alert).toHaveTextContent('No finding F-9999');
  });
});

describe('DCAA cost accounting finding (F-3315, rule L-11)', () => {
  const l11 = samples.findingDetailL11;

  it('renders the domain with the rule, and the headline and status text', async () => {
    installFakeApi();
    renderApp('/findings/F-3315');
    const h1 = await screen.findByRole('heading', { level: 1 });
    expect(h1).toHaveTextContent('F-3315');
    expect(h1).toHaveTextContent(l11.headline);
    const meta = screen.getByText(/^Rule L-11/).closest('ul')!;
    expect(meta).toHaveTextContent('Domain DCAA cost accounting');
    expect(meta).toHaveTextContent('High');
    expect(meta).toHaveTextContent('Open');
    expect(meta).toHaveTextContent('$23,477.34');
  });

  it('renders the sections in the mandated order: what happened, why it matters, impact, action, then evidence, disposition, history', async () => {
    installFakeApi();
    renderApp('/findings/F-3315');
    const els = [];
    for (const name of HEADINGS) els.push(await screen.findByRole('heading', { level: 2, name }));
    await screen.findByRole('table', { name: /source records behind this finding/i });
    for (let i = 0; i < els.length - 1; i++) {
      expect(els[i].compareDocumentPosition(els[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING, `${HEADINGS[i]} must precede ${HEADINGS[i + 1]}`).toBeTruthy();
    }
    // the four explanation texts come from the payload and sit above the evidence table
    const table = screen.getByRole('table', { name: /source records behind this finding/i });
    for (const text of Object.values(l11.explanation)) {
      const el = screen.getByText(text);
      expect(el.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    const why = screen.getByRole('heading', { name: 'Why it matters' }).closest('section')!;
    for (const a of l11.authorities) expect(within(why).getByText(a)).toBeInTheDocument();
  });

  it('renders every computed value as text, including the long history string, inside the computed block', async () => {
    installFakeApi();
    renderApp('/findings/F-3315');
    await screen.findByRole('heading', { name: 'Impact' });
    const block = screen.getByText('Values the rule computed').closest('details')!;
    for (const [k, v] of Object.entries(l11.computed)) {
      const dt = within(block).getByText(k, { selector: 'dt' });
      expect(dt.closest('div')).toHaveTextContent(v);
    }
    expect(within(block).getByText(l11.computed.history)).toBeInTheDocument();
    expect(block).toHaveTextContent('threshold tripped');
    expect(block).toHaveTextContent(l11.severity_reason);
  });

  it('lets a long unbroken computed value wrap instead of widening the page (stylesheet guard)', () => {
    const css = fs.readFileSync(path.resolve(__dirname, '..', 'styles.css'), 'utf8');
    const blocks = css.match(/\.detail-list dd\s*\{[^}]*\}/g) ?? [];
    expect(blocks.some((b) => /overflow-wrap:\s*anywhere/.test(b) && /min-width:\s*0/.test(b))).toBe(true);
  });

  it('renders a computed value that is very long without crashing', async () => {
    const long = 'x'.repeat(400);
    installFakeApi([(c) => (c.path === '/api/findings/F-3315' ? { body: { ...l11, computed: { ...l11.computed, history: long } } } : undefined)]);
    renderApp('/findings/F-3315');
    expect(await screen.findByText(long)).toBeInTheDocument();
  });

  it('offers the moves the API allows and shows the history row', async () => {
    installFakeApi();
    renderApp('/findings/F-3315');
    const sec = (await screen.findByRole('heading', { name: 'Disposition' })).closest('section')!;
    expect(within(sec).getAllByRole('button')).toHaveLength(1);
    expect(within(sec).getByRole('button', { name: 'Move to in review' })).toBeInTheDocument();
    expect(await screen.findByText('Opened by RUN-2026-08-MER-001')).toBeInTheDocument();
  });
});

describe('disposition controls', () => {
  it('offers exactly the buttons in allowed_transitions (open finding: in_review only)', async () => {
    installFakeApi();
    renderApp('/findings/F-3311');
    const sec = (await screen.findByRole('heading', { name: 'Disposition' })).closest('section')!;
    const shown = Array.from(sec.querySelectorAll('button[data-transition]')).map((b) => (b as HTMLElement).dataset.transition);
    expect(shown).toEqual(samples.findingDetail.allowed_transitions);
    expect(within(sec).getAllByRole('button')).toHaveLength(1);
    expect(within(sec).getByRole('button', { name: 'Move to in review' })).toBeInTheDocument();
  });

  it('offers exactly the buttons in allowed_transitions (in review finding: three)', async () => {
    installFakeApi([(c) => (c.method === 'GET' && c.path === '/api/findings/F-3311' ? { body: samples.dispositionResponse } : undefined)]);
    renderApp('/findings/F-3311');
    const sec = (await screen.findByRole('heading', { name: 'Disposition' })).closest('section')!;
    const shown = Array.from(sec.querySelectorAll('button[data-transition]')).map((b) => (b as HTMLElement).dataset.transition);
    expect(shown).toEqual(['confirmed', 'legit_exception', 'data_error']);
    expect(within(sec).getAllByRole('button')).toHaveLength(3);
    expect(within(sec).queryByRole('button', { name: /remediated|close|reopen|in review/i })).toBeNull();
  });

  it('offers no buttons when the API allows no transition', async () => {
    installFakeApi([
      (c) => (c.method === 'GET' && c.path === '/api/findings/F-3311' ? { body: { ...samples.findingDetail, status: 'closed', allowed_transitions: [] } } : undefined),
    ]);
    renderApp('/findings/F-3311');
    const sec = (await screen.findByRole('heading', { name: 'Disposition' })).closest('section')!;
    expect(within(sec).queryAllByRole('button')).toHaveLength(0);
    expect(sec).toHaveTextContent(/No status change is available/);
  });

  it('blocks legit_exception without a reason code and a note, and sends nothing', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi([(c) => (c.method === 'GET' && c.path === '/api/findings/F-3311' ? { body: samples.dispositionResponse } : undefined)]);
    renderApp('/findings/F-3311');
    const sec = (await screen.findByRole('heading', { name: 'Disposition' })).closest('section')!;
    const s = within(sec);
    await user.type(s.getByLabelText('Acting as'), 'a.okafor');
    const post = () => calls.filter((c) => c.method === 'POST');

    // neither
    await user.click(s.getByRole('button', { name: 'Mark legitimate exception' }));
    expect(await s.findByRole('alert')).toHaveTextContent(/reason code and a note/i);
    expect(post()).toHaveLength(0);

    // reason only
    await user.selectOptions(s.getByLabelText('Reason code'), 'approved_exception');
    await user.click(s.getByRole('button', { name: 'Mark legitimate exception' }));
    expect(s.getByRole('alert')).toHaveTextContent(/reason code and a note/i);
    expect(post()).toHaveLength(0);

    // note only (whitespace does not count)
    await user.selectOptions(s.getByLabelText('Reason code'), '');
    fireEvent.change(s.getByLabelText('Note'), { target: { value: '   ' } });
    await user.click(s.getByRole('button', { name: 'Mark legitimate exception' }));
    expect(post()).toHaveLength(0);

    // both: the request goes out with reason_code, note and actor
    await user.selectOptions(s.getByLabelText('Reason code'), 'approved_exception');
    fireEvent.change(s.getByLabelText('Note'), { target: { value: 'Signed correction memo on file' } });
    await user.click(s.getByRole('button', { name: 'Mark legitimate exception' }));
    await waitFor(() => expect(post()).toHaveLength(1));
    expect(post()[0].path).toBe('/api/findings/F-3311/disposition');
    expect(post()[0].body).toEqual({
      disposition: 'legit_exception',
      actor: 'a.okafor',
      reason_code: 'approved_exception',
      note: 'Signed correction memo on file',
    });
  });

  it('moves an open finding to in review and shows the returned status and history', async () => {
    const user = userEvent.setup();
    const { calls } = installFakeApi();
    renderApp('/findings/F-3311');
    const sec = (await screen.findByRole('heading', { name: 'Disposition' })).closest('section')!;
    await user.type(within(sec).getByLabelText('Acting as'), 'a.okafor');
    await user.click(within(sec).getByRole('button', { name: 'Move to in review' }));
    await screen.findByText('Assigned to the C-8841 project accountant');
    expect(within(sec).getByRole('status')).toHaveTextContent('In review');
    const post = calls.find((c) => c.method === 'POST')!;
    expect(post.body).toEqual({ disposition: 'in_review', actor: 'a.okafor' });
    // the buttons are now those the API returned for the new state
    expect(within(sec).getByRole('button', { name: 'Mark data error' })).toBeInTheDocument();
  });

  it('shows the API error code and message when a disposition is refused', async () => {
    const user = userEvent.setup();
    installFakeApi([
      (c) =>
        c.method === 'POST' && c.path.endsWith('/disposition')
          ? { status: 409, body: { error: { code: 'invalid_transition', message: 'open -> confirmed is not allowed' } } }
          : undefined,
    ]);
    renderApp('/findings/F-3311');
    const sec = (await screen.findByRole('heading', { name: 'Disposition' })).closest('section')!;
    await user.type(within(sec).getByLabelText('Acting as'), 'a.okafor');
    await user.click(within(sec).getByRole('button', { name: 'Move to in review' }));
    const alert = await within(sec).findByRole('alert');
    expect(alert).toHaveTextContent('invalid_transition');
    expect(alert).toHaveTextContent('open -> confirmed is not allowed');
  });
});
