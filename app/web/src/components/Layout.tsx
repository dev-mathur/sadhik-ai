import { useEffect, useRef } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { DISCLAIMER } from '../lib/copy';
import { Icons } from '../lib/icons';

const NAV = [
  { to: '/', label: 'Status', end: true, icon: Icons.Pulse },
  { to: '/findings', label: 'Findings', end: false, icon: Icons.List },
  { to: '/rules', label: 'Rules & config', end: false, icon: Icons.Sliders },
  { to: '/runs', label: 'Runs', end: false, icon: Icons.History },
];

export function Layout() {
  const mainRef = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  const first = useRef(true);

  // Move focus to the main region after a client-side navigation so keyboard and screen-reader users land on the new page.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [pathname]);

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="site-header no-print">
        <div className="site-header-inner">
          <p className="brand">
            <span className="brand-mark" aria-hidden="true">
              <Icons.Tick size={18} />
            </span>
            <span className="brand-text">
              <span className="brand-name">Sadhik AI</span>
              <span className="brand-sub">Compliance reconciliation</span>
            </span>
          </p>
          <nav aria-label="Primary">
            <ul className="nav-list">
              {NAV.map(({ to, label, end, icon: Icon }) => (
                <li key={to}>
                  <NavLink to={to} end={end} className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}>
                    <Icon size={16} className="nav-icon" />
                    <span>{label}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>
      <main id="main" ref={mainRef} tabIndex={-1} className="site-main">
        <Outlet />
      </main>
      <footer className="site-footer no-print">
        <p>{DISCLAIMER}</p>
      </footer>
    </div>
  );
}
