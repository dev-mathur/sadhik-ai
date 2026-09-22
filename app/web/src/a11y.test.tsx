import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { installFakeApi } from './test/fakeApi';
import { renderApp } from './test/renderApp';

const DISCLAIMER = 'This is an analysis of submitted data. It is not an audit opinion or an attestation.';

describe('layout and accessibility', () => {
  for (const route of ['/', '/findings', '/findings?domain=dcaa_cost_accounting', '/findings/F-3311', '/findings/F-3315', '/rules', '/runs']) {
    it(`renders landmarks and the verbatim footer on ${route}`, async () => {
      installFakeApi();
      renderApp(route);
      expect(screen.getByRole('banner')).toBeInTheDocument();
      expect(screen.getByRole('navigation', { name: 'Primary' })).toBeInTheDocument();
      expect(screen.getByRole('main')).toBeInTheDocument();
      expect(within(screen.getByRole('contentinfo')).getByText(DISCLAIMER)).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveAttribute('href', '#main');
      await screen.findByRole('heading', { level: 1 });
    });
  }

  it('marks the current page in the navigation', async () => {
    installFakeApi();
    renderApp('/rules');
    expect(screen.getByRole('link', { name: 'Rules & config' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'Status' })).not.toHaveAttribute('aria-current');
    await screen.findByRole('heading', { level: 1 });
  });

  it('conveys severity, status and result as text, not colour alone', async () => {
    installFakeApi();
    renderApp('/findings');
    const table = await screen.findByRole('table');
    for (const cell of within(table).getAllByRole('cell')) {
      // every badge cell has readable text
      if (cell.querySelector('.badge')) expect(cell.textContent?.trim().length).toBeGreaterThan(2);
    }
    expect(within(table).getAllByText('High').length).toBeGreaterThan(0);
    expect(within(table).getAllByText('Low').length).toBeGreaterThan(0);
    // glyphs are decorative and hidden from assistive tech; the text carries the meaning
    expect(table.querySelectorAll('.badge .glyph[aria-hidden="true"]').length).toBeGreaterThan(0);
  });
});
