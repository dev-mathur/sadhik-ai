// Mock of the Sadhak REST API (contracts/api.md) for developing the UI without the backend.
// Serves ../contracts/sample_payloads. Plain Node, no dependencies. Port 8000 by default (override with the PORT env var).
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const dir = process.env.SAMPLES_DIR ?? path.resolve(here, '../contracts/sample_payloads');
const port = Number(process.env.PORT ?? 8000);

const load = (name) => JSON.parse(fs.readFileSync(path.join(dir, `${name}.json`), 'utf8'));

function send(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'content-length': Buffer.byteLength(text) });
  res.end(text);
}

const notFound = (res, what) => send(res, 404, { error: { code: 'not_found', message: `No such resource: ${what}` } });

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => (data += c));
    req.on('end', () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch {
        resolve(null);
      }
    });
  });
}

function configResult(body) {
  const yamlText = body && typeof body.yaml === 'string' ? body.yaml : '';
  return yamlText.includes('REJECT_ME')
    ? { status: 422, payload: load('config_rejected') }
    : { status: 200, payload: load('config_accepted') };
}

// Domain ids come from the status sample, so the mock rejects an unknown ?domain= the way the real API does (400 bad_filter).
function badDomain(res, id) {
  const known = load('status').domains.map((d) => d.id);
  if (!id || known.includes(id)) return false;
  send(res, 400, { error: { code: 'bad_filter', message: `Unknown domain: ${id}` } });
  return true;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
  const p = url.pathname.replace(/\/+$/, '') || '/';
  const m = req.method ?? 'GET';
  let match;

  try {
    if (m === 'GET' && p === '/api/health') return send(res, 200, { ok: true });
    if (m === 'GET' && p === '/api/status') return send(res, 200, load('status'));

    if (m === 'GET' && p === '/api/findings') {
      const list = load('findings_list');
      const q = url.searchParams;
      let items = list.items;
      if (badDomain(res, q.get('domain'))) return;
      for (const [param, field] of [['severity', 'severity'], ['rule', 'rule_id'], ['status', 'status'], ['period', 'period'], ['domain', 'domain']]) {
        const v = q.get(param);
        if (v) items = items.filter((it) => it[field] === v);
      }
      return send(res, 200, { ...list, total: items.length, items });
    }
    if (m === 'GET' && (match = p.match(/^\/api\/findings\/([^/]+)\/evidence$/))) return send(res, 200, load('evidence'));
    if (m === 'POST' && (match = p.match(/^\/api\/findings\/([^/]+)\/disposition$/))) {
      const body = await readBody(req);
      if (body === null) return send(res, 400, { error: { code: 'bad_request', message: 'Body is not valid JSON' } });
      if (body.disposition === 'legit_exception' && (!body.reason_code || !body.note)) {
        return send(res, 422, { error: { code: 'reason_required', message: 'legit_exception requires reason_code and note' } });
      }
      return send(res, 200, load('disposition_response'));
    }
    if (m === 'GET' && (match = p.match(/^\/api\/findings\/([^/]+)$/))) {
      // F-3315 is the DCAA finding sample; every other id serves the labor sample.
      return send(res, 200, load(decodeURIComponent(match[1]) === 'F-3315' ? 'finding_detail_l11' : 'finding_detail'));
    }

    if (m === 'GET' && p === '/api/rules') {
      const list = load('rules_list');
      const domain = url.searchParams.get('domain');
      if (badDomain(res, domain)) return;
      return send(res, 200, domain ? { ...list, items: list.items.filter((r) => r.domain === domain) } : list);
    }
    if (m === 'GET' && (match = p.match(/^\/api\/rules\/([^/]+)$/))) return send(res, 200, load('rule_detail'));

    if (m === 'GET' && p === '/api/config') return send(res, 200, load('config_get'));
    if (m === 'POST' && p === '/api/config/validate') {
      const r = configResult(await readBody(req));
      return send(res, r.status, r.payload);
    }
    if (m === 'PUT' && p === '/api/config') {
      const r = configResult(await readBody(req));
      return send(res, r.status, r.payload);
    }

    if (m === 'GET' && p === '/api/runs') return send(res, 200, load('runs_list'));
    if (m === 'POST' && p === '/api/runs') return send(res, 200, load('run_created'));
    if (m === 'POST' && (match = p.match(/^\/api\/runs\/([^/]+)\/replay$/))) return send(res, 200, load('replay_result'));
    if (m === 'GET' && (match = p.match(/^\/api\/runs\/([^/]+)$/))) return send(res, 200, load('run_detail'));

    return notFound(res, `${m} ${p}`);
  } catch (err) {
    return send(res, 500, { error: { code: 'mock_error', message: String(err && err.message ? err.message : err) } });
  }
});

server.listen(port, () => console.log(`mock API listening on http://localhost:${port} (samples: ${dir})`));
