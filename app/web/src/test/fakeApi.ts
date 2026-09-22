// A fetch fake that serves the frozen sample payloads (contracts/sample_payloads) so the UI is tested against the contract.
import { vi } from 'vitest';
import configAccepted from '../../../contracts/sample_payloads/config_accepted.json';
import configGet from '../../../contracts/sample_payloads/config_get.json';
import configRejected from '../../../contracts/sample_payloads/config_rejected.json';
import dispositionResponse from '../../../contracts/sample_payloads/disposition_response.json';
import evidence from '../../../contracts/sample_payloads/evidence.json';
import findingDetail from '../../../contracts/sample_payloads/finding_detail.json';
import findingDetailL11 from '../../../contracts/sample_payloads/finding_detail_l11.json';
import findingsList from '../../../contracts/sample_payloads/findings_list.json';
import replayResult from '../../../contracts/sample_payloads/replay_result.json';
import rulesList from '../../../contracts/sample_payloads/rules_list.json';
import runCreated from '../../../contracts/sample_payloads/run_created.json';
import runDetail from '../../../contracts/sample_payloads/run_detail.json';
import runsList from '../../../contracts/sample_payloads/runs_list.json';
import status from '../../../contracts/sample_payloads/status.json';

export const samples = {
  configAccepted,
  configGet,
  configRejected,
  dispositionResponse,
  evidence,
  findingDetail,
  findingDetailL11,
  findingsList,
  replayResult,
  rulesList,
  runCreated,
  runDetail,
  runsList,
  status,
};

export interface Call {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

type Handler = (call: Call) => { status?: number; body: unknown } | undefined;

/** Installs a global fetch fake. `overrides` run first; a handler returns undefined to fall through to the samples. */
export function installFakeApi(overrides: Handler[] = []) {
  const calls: Call[] = [];
  const fake = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost');
    const call: Call = {
      method: (init?.method ?? 'GET').toUpperCase(),
      path: url.pathname,
      search: url.search,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(call);
    const reply = (body: unknown, code = 200) =>
      new Response(JSON.stringify(body), { status: code, headers: { 'content-type': 'application/json' } });

    for (const o of overrides) {
      const r = o(call);
      if (r) return reply(r.body, r.status ?? 200);
    }

    const { method, path } = call;
    if (method === 'GET' && path === '/api/status') return reply(status);
    if (method === 'GET' && path === '/api/findings') {
      // Filters mirror the real API for the params the tests use.
      const q = new URLSearchParams(call.search);
      let items = findingsList.items;
      for (const [param, field] of [['severity', 'severity'], ['rule', 'rule_id'], ['status', 'status'], ['domain', 'domain']] as const) {
        const v = q.get(param);
        if (v) items = items.filter((it) => it[field] === v);
      }
      return reply({ total: items.length, items });
    }
    if (method === 'GET' && /^\/api\/findings\/[^/]+\/evidence$/.test(path)) return reply(evidence);
    if (method === 'POST' && /^\/api\/findings\/[^/]+\/disposition$/.test(path)) return reply(dispositionResponse);
    if (method === 'GET' && path === '/api/findings/F-3315') return reply(findingDetailL11);
    if (method === 'GET' && /^\/api\/findings\/[^/]+$/.test(path)) return reply(findingDetail);
    if (method === 'GET' && path === '/api/rules') {
      const domain = new URLSearchParams(call.search).get('domain');
      return reply(domain ? { ...rulesList, items: rulesList.items.filter((r) => r.domain === domain) } : rulesList);
    }
    if (method === 'GET' && path === '/api/config') return reply(configGet);
    if ((method === 'POST' && path === '/api/config/validate') || (method === 'PUT' && path === '/api/config')) {
      const yaml = (call.body as { yaml?: string } | undefined)?.yaml ?? '';
      return yaml.includes('REJECT_ME') ? reply(configRejected, 422) : reply(configAccepted);
    }
    if (method === 'GET' && path === '/api/runs') return reply(runsList);
    if (method === 'POST' && path === '/api/runs') return reply(runCreated);
    if (method === 'POST' && /^\/api\/runs\/[^/]+\/replay$/.test(path)) return reply(replayResult);
    if (method === 'GET' && /^\/api\/runs\/[^/]+$/.test(path)) return reply(runDetail);
    return reply({ error: { code: 'not_found', message: `unhandled ${method} ${path}` } }, 404);
  });
  vi.stubGlobal('fetch', fake);
  return { calls, fake };
}
