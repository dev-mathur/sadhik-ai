import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ApiError } from '../api/client';
import type { ConfigGet, ConfigResult } from '../api/types';
import { toApiError } from '../hooks';
import { Icons } from '../lib/icons';
import { Glyph } from './Glyph';
import { ErrorBox, TableWrap } from './States';

/** Character range of the 1-based `line` in `text`. */
function lineRange(text: string, line: number): [number, number] {
  const lines = text.split('\n');
  let start = 0;
  for (let i = 0; i < Math.min(line - 1, lines.length); i++) start += lines[i].length + 1;
  const len = lines[Math.min(line, lines.length) - 1]?.length ?? 0;
  return [start, start + len];
}

export function ConfigEditor({ config, onSaved }: { config: ConfigGet; onSaved: () => void }) {
  const [text, setText] = useState(config.yaml);
  const [busy, setBusy] = useState<'validate' | 'save' | undefined>();
  const [result, setResult] = useState<{ kind: 'validate' | 'save'; value: ConfigResult } | undefined>();
  const [error, setError] = useState<ApiError | undefined>();
  const ref = useRef<HTMLTextAreaElement>(null);

  // A newly loaded active version replaces the editor contents.
  useEffect(() => {
    setText(config.yaml);
  }, [config.yaml, config.config_version]);

  const edited = text !== config.yaml;

  async function run(kind: 'validate' | 'save') {
    setBusy(kind);
    setError(undefined);
    setResult(undefined);
    try {
      const value = kind === 'validate' ? await api.validateConfig(text) : await api.saveConfig(text);
      setResult({ kind, value });
      if (kind === 'save' && value.accepted) onSaved();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(undefined);
    }
  }

  function goToLine(line: number) {
    const ta = ref.current;
    if (!ta) return;
    const [s, e] = lineRange(text, line);
    ta.focus();
    ta.setSelectionRange(s, e);
    const lineHeight = parseFloat(getComputedStyle(ta).lineHeight);
    if (Number.isFinite(lineHeight)) ta.scrollTop = Math.max(0, (line - 3) * lineHeight);
  }

  return (
    <div>
      <div className="field wide">
        <label htmlFor="config-yaml">Active rules configuration (YAML)</label>
        <textarea
          id="config-yaml"
          ref={ref}
          className="code-area"
          rows={22}
          spellCheck={false}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-describedby="config-hint"
        />
        <p id="config-hint" className="hint">
          Version {config.config_version} {'·'} sha256 {config.sha256} {'·'} floor registry {config.floor_registry_version}.{' '}
          {edited ? 'You have unsaved edits.' : 'No unsaved edits.'} Validate checks the text without saving; Save stores a new version only if it is accepted.
        </p>
      </div>
      <div className="button-row">
        <button type="button" onClick={() => run('validate')} disabled={busy !== undefined}>
          {busy === 'validate' ? 'Validating…' : 'Validate'}
        </button>
        <button type="button" className="button-primary" onClick={() => run('save')} disabled={busy !== undefined}>
          {busy === 'save' ? 'Saving…' : 'Save'}
        </button>
        <button type="button" onClick={() => { setText(config.yaml); setResult(undefined); setError(undefined); }} disabled={!edited}>
          Revert to active version
        </button>
      </div>

      {error ? <ErrorBox error={error} /> : null}

      {result ? (
        <div role={result.value.accepted ? 'status' : 'alert'} className={`state ${result.value.accepted ? 'state-ok' : 'state-error'}`} id="config-result">
          {result.value.accepted ? (
            <>
              <p>
                <strong>
                  <Glyph icon={Icons.CheckCircle} size={16} />
                  {result.kind === 'save' ? 'Accepted and saved' : 'Accepted (not saved)'}
                </strong>
                {result.value.config_version !== null ? <> {'·'} version {result.value.config_version}</> : null}
                {result.value.sha256 ? <> {'·'} sha256 {result.value.sha256}</> : null}
              </p>
            </>
          ) : (
            <p>
              <strong>
                <Glyph icon={Icons.XCircle} size={16} />
                Rejected {'·'} nothing was saved
              </strong>
            </p>
          )}

          {result.value.errors.length > 0 ? (
            <TableWrap label="Configuration errors">
              <table>
                <caption className="visually-hidden">Errors found in the configuration</caption>
                <thead>
                  <tr>
                    <th scope="col">Line</th>
                    <th scope="col">Code</th>
                    <th scope="col">Path</th>
                    <th scope="col">Message</th>
                  </tr>
                </thead>
                <tbody>
                  {result.value.errors.map((er, i) => (
                    <tr key={`${er.line}-${er.code}-${i}`}>
                      <th scope="row">
                        {er.line === null || er.line === undefined ? (
                          <span className="muted">No line</span>
                        ) : (
                          <button type="button" className="link-button" onClick={() => goToLine(er.line as number)} aria-label={`Line ${er.line}, go to line in editor`}>
                            {er.line}
                          </button>
                        )}
                      </th>
                      <td>
                        <code>{er.code}</code>
                      </td>
                      <td>
                        <code>{er.path}</code>
                      </td>
                      <td>{er.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          ) : null}

          {result.value.warnings.length > 0 ? (
            <>
              <h3>Warnings</h3>
              <ul>
                {result.value.warnings.map((w, i) => (
                  <li key={i}>
                    <Glyph icon={Icons.TriangleOutline} size={15} />
                    {w}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
