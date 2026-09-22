// Copy and accessibility guardrails that read the source files themselves.
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

// The only sentence allowed to contain a restricted word. Must match contracts/ENGINE_API.md exactly.
const DISCLAIMER = 'This is an analysis of submitted data. It is not an audit opinion or an attestation.';

// Patterns are assembled from fragments so this file does not itself contain the words it forbids.
// Terms: 1 the c-word incl. its negated form, 2 the c-word for certification, 3 the audit-r word, 4 the approval phrase, 5 the a-word family.
const RESTRICTED: [string, RegExp][] = [
  ['restricted term 1', new RegExp('compl' + 'iant', 'i')],
  ['restricted term 2', new RegExp('certi' + 'fied', 'i')],
  ['restricted term 3', new RegExp('audit[-\\s]?' + 'ready', 'i')],
  ['restricted term 4', new RegExp('dcaa[-\\s]?' + 'approved', 'i')],
  ['restricted term 5', new RegExp('att' + 'est', 'i')],
];

const webRoot = path.resolve(__dirname, '..');

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

const files = [...walk(__dirname), path.join(webRoot, 'index.html'), path.join(webRoot, 'mock-server.mjs')];

describe('copy rules', () => {
  it('scans a meaningful set of files', () => {
    expect(files.length).toBeGreaterThan(20);
    expect(files.some((f) => f.endsWith('styles.css'))).toBe(true);
    expect(files.some((f) => f.endsWith('Layout.tsx'))).toBe(true);
  });

  it('no file under src/ (or index.html, mock-server.mjs) contains a restricted term, after removing the exact footer string', () => {
    const offenders: string[] = [];
    for (const f of files) {
      const text = fs.readFileSync(f, 'utf8').split(DISCLAIMER).join('');
      for (const [name, re] of RESTRICTED) {
        const m = re.exec(text);
        if (m) offenders.push(`${path.relative(webRoot, f)}: ${name} near "${text.slice(Math.max(0, m.index - 20), m.index + 30).replace(/\s+/g, ' ')}"`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('the footer string in the app is exactly the required disclaimer', () => {
    const copy = fs.readFileSync(path.join(__dirname, 'lib', 'copy.ts'), 'utf8');
    expect(copy).toContain(`'${DISCLAIMER}'`);
  });

  it('allows the domain name "DCAA cost accounting" but still rejects the approval phrase', () => {
    const name = 'DCAA cost accounting';
    expect(RESTRICTED.some(([, re]) => re.test(name))).toBe(false);
    for (const bad of ['DCAA-' + 'approved', 'DCAA ' + 'approved', 'dcaa' + 'approved']) {
      expect(RESTRICTED.some(([, re]) => re.test(bad)), bad).toBe(true);
    }
  });

  it('the guard itself catches a restricted term (sanity check)', () => {
    const bad = 'This finding is ' + 'non-compl' + 'iant.';
    expect(RESTRICTED.some(([, re]) => re.test(bad))).toBe(true);
    expect(RESTRICTED.some(([, re]) => re.test(DISCLAIMER.split(DISCLAIMER).join('')))).toBe(false);
    // the disclaimer is only exempt as an exact string
    expect(RESTRICTED.some(([, re]) => re.test(DISCLAIMER.replace('This is', 'That is')))).toBe(true);
  });
});
