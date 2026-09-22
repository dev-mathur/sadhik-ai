import type {
  ConfigGet,
  ConfigResult,
  DispositionRequest,
  EvidencePage,
  FindingDetail,
  FindingsList,
  ReplayResult,
  RulesList,
  RunCreated,
  RunDetail,
  RunsList,
  Status,
} from './types';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

function errorFrom(status: number, json: unknown, statusText: string): ApiError {
  const e = (json as { error?: { code?: unknown; message?: unknown } } | null)?.error;
  const code = typeof e?.code === 'string' ? e.code : `http_${status}`;
  const message = typeof e?.message === 'string' ? e.message : statusText || 'Request failed';
  return new ApiError(status, code, message);
}

async function request(method: string, path: string, body?: unknown): Promise<{ res: Response; json: unknown }> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers: body === undefined ? { accept: 'application/json' } : { accept: 'application/json', 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (err) {
    throw new ApiError(0, 'network_error', err instanceof Error ? err.message : 'The API could not be reached');
  }
  const text = await res.text();
  let json: unknown = null;
  if (text) {
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
  }
  return { res, json };
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const { res, json } = await request(method, path, body);
  if (!res.ok) throw errorFrom(res.status, json, res.statusText);
  return json as T;
}

// Config validate/save answer 422 with a ConfigResult body ({accepted:false, errors:[...]}) for a rejected file.
// That is a normal outcome, not a transport error, so it is returned rather than thrown.
async function callConfig(method: string, path: string, body: unknown): Promise<ConfigResult> {
  const { res, json } = await request(method, path, body);
  if (json && typeof (json as ConfigResult).accepted === 'boolean') return json as ConfigResult;
  throw errorFrom(res.status, json, res.statusText);
}

function qs(params: Record<string, string | number | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : '';
}

const enc = encodeURIComponent;

export const api = {
  status: (p: { period?: string; as_of?: string } = {}) => call<Status>('GET', `/api/status${qs(p)}`),
  findings: (p: { severity?: string; rule?: string; status?: string; period?: string; domain?: string } = {}) =>
    call<FindingsList>('GET', `/api/findings${qs(p)}`),
  finding: (id: string) => call<FindingDetail>('GET', `/api/findings/${enc(id)}`),
  evidence: (id: string, page = 1, pageSize = 50) =>
    call<EvidencePage>('GET', `/api/findings/${enc(id)}/evidence${qs({ page, page_size: pageSize })}`),
  disposition: (id: string, body: DispositionRequest) =>
    call<FindingDetail>('POST', `/api/findings/${enc(id)}/disposition`, body),
  rules: (p: { domain?: string } = {}) => call<RulesList>('GET', `/api/rules${qs(p)}`),
  config: () => call<ConfigGet>('GET', '/api/config'),
  validateConfig: (yaml: string) => callConfig('POST', '/api/config/validate', { yaml }),
  saveConfig: (yaml: string) => callConfig('PUT', '/api/config', { yaml }),
  runs: () => call<RunsList>('GET', '/api/runs'),
  run: (id: string) => call<RunDetail>('GET', `/api/runs/${enc(id)}`),
  createRun: (period: string, tier: string) => call<RunCreated>('POST', '/api/runs', { period, tier }),
  replay: (id: string) => call<ReplayResult>('POST', `/api/runs/${enc(id)}/replay`),
};
