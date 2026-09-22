import type { ReactNode } from 'react';
import type { ApiError } from '../api/client';
import { Icons } from '../lib/icons';
import { Glyph } from './Glyph';

export function Loading({ what }: { what: string }) {
  return (
    <p role="status" className="state state-loading">
      <span className="spinner" aria-hidden="true">
        <Icons.Refresh size={16} />
      </span>
      Loading {what}
      {'…'}
    </p>
  );
}

export function ErrorBox({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div role="alert" className="state state-error">
      <Glyph icon={Icons.Alert} size={20} />
      <div className="state-body">
        <strong>The request failed.</strong>
        <div>
          <span className="visually-label">Error code:</span> <code>{error.code}</code>
        </div>
        <div>
          <span className="visually-label">Message:</span> {error.message}
        </div>
        {onRetry ? (
          <div className="state-actions">
            <button type="button" onClick={onRetry} className="button button-primary">
              Try again
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="state state-empty">
      <Glyph icon={Icons.Info} size={16} />
      {children}
    </p>
  );
}

/** Scrollable table container that is reachable and scrollable by keyboard. */
export function TableWrap({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
