import { useState } from 'react';
import { api } from '../api/client';
import type { ApiError } from '../api/client';
import type { FindingDetail } from '../api/types';
import { toApiError } from '../hooks';
import { REASON_CODES, STATUS_LABEL, TRANSITION_LABEL, humanize } from '../lib/format';
import { Icons } from '../lib/icons';
import { Glyph } from './Glyph';
import { ErrorBox } from './States';

const ACTOR_KEY = 'sadhik.actor';

function readActor(): string {
  try {
    return window.localStorage.getItem(ACTOR_KEY) ?? '';
  } catch {
    return '';
  }
}
function saveActor(v: string) {
  try {
    window.localStorage.setItem(ACTOR_KEY, v);
  } catch {
    /* storage unavailable: the field simply is not remembered */
  }
}

export function Disposition({ finding, onUpdated }: { finding: FindingDetail; onUpdated: (f: FindingDetail) => void }) {
  const [actor, setActor] = useState(readActor);
  const [reasonCode, setReasonCode] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | undefined>();
  const [apiError, setApiError] = useState<ApiError | undefined>();
  const [done, setDone] = useState<string | undefined>();

  const allowed = finding.allowed_transitions;

  async function submit(to: string) {
    setDone(undefined);
    setApiError(undefined);
    if (!actor.trim()) {
      setProblem('Enter who is making this change (your name or ID).');
      return;
    }
    if (to === 'legit_exception' && (!reasonCode || !note.trim())) {
      setProblem('A legitimate exception needs both a reason code and a note.');
      return;
    }
    setProblem(undefined);
    setBusy(true);
    try {
      const body = {
        disposition: to,
        actor: actor.trim(),
        ...(reasonCode ? { reason_code: reasonCode } : {}),
        ...(note.trim() ? { note: note.trim() } : {}),
      };
      const updated = await api.disposition(finding.finding_id, body);
      saveActor(actor.trim());
      onUpdated(updated);
      setDone(STATUS_LABEL[updated.status] ?? humanize(updated.status));
      setReasonCode('');
      setNote('');
    } catch (e) {
      setApiError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="sec-disposition" id="disposition" className="card">
      <h2 id="sec-disposition">Disposition</h2>
      {allowed.length === 0 ? (
        <p>
          No status change is available for a finding that is {(STATUS_LABEL[finding.status] ?? finding.status).toLowerCase()}.
          {finding.status === 'closed' ? ' A closed finding reopens automatically if the same condition appears in a later run.' : ''}
        </p>
      ) : (
        <>
          <p className="muted">Every change is recorded in the history below with who made it, when, and why.</p>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="disp-actor">Acting as</label>
              <input id="disp-actor" value={actor} onChange={(e) => setActor(e.target.value)} autoComplete="username" />
            </div>
            <div className="field">
              <label htmlFor="disp-reason">Reason code</label>
              <select id="disp-reason" value={reasonCode} onChange={(e) => setReasonCode(e.target.value)}>
                <option value="">Select a reason code</option>
                {REASON_CODES.map((r) => (
                  <option key={r.code} value={r.code}>
                    {r.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="field wide">
              <label htmlFor="disp-note">Note</label>
              <textarea id="disp-note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} aria-describedby="disp-hint" />
              <p id="disp-hint" className="hint">
                A reason code and a note are required to mark a legitimate exception. They are optional for other changes.
              </p>
            </div>
          </div>
          <div className="button-row" role="group" aria-label="Available status changes">
            {allowed.map((t) => (
              <button key={t} type="button" data-transition={t} disabled={busy} onClick={() => submit(t)}>
                {TRANSITION_LABEL[t] ?? `Move to ${humanize(t).toLowerCase()}`}
              </button>
            ))}
          </div>
          {problem ? (
            <p role="alert" className="state state-error">
              <Glyph icon={Icons.XCircle} size={16} />
              {problem}
            </p>
          ) : null}
          {apiError ? <ErrorBox error={apiError} /> : null}
          {done ? (
            <p role="status" className="state state-ok">
              <Glyph icon={Icons.CheckCircle} size={16} />
              Status is now {done}.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
