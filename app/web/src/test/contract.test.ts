// Compile-time check that every sample payload satisfies the hand-written types in src/api/types.ts.
// If a payload and a type drift apart, `npm run typecheck` fails here.
import { describe, expect, it } from 'vitest';
import type {
  ConfigGet,
  DomainState,
  ConfigResult,
  EvidencePage,
  FindingDetail,
  FindingsList,
  FindingSummary,
  ReplayResult,
  Rule,
  RulesList,
  RunCreated,
  RunDetail,
  RunsList,
  Status,
} from '../api/types';
import { samples } from './fakeApi';

describe('sample payloads match the API types', () => {
  it('type-checks every payload', () => {
    const status: Status = samples.status;
    const findings: FindingsList = samples.findingsList;
    const detail: FindingDetail = samples.findingDetail;
    const dcaaDetail: FindingDetail = samples.findingDetailL11;
    const summary: FindingSummary = samples.findingsList.items[0];
    const domains: DomainState[] = samples.status.domains;
    const disposition: FindingDetail = samples.dispositionResponse;
    const evidence: EvidencePage = samples.evidence;
    const rules: RulesList = samples.rulesList;
    const rule: Rule = samples.rulesList.items[0];
    const config: ConfigGet = samples.configGet;
    const accepted: ConfigResult = samples.configAccepted;
    const rejected: ConfigResult = samples.configRejected;
    const runs: RunsList = samples.runsList;
    const run: RunDetail = samples.runDetail;
    const created: RunCreated = samples.runCreated;
    const replay: ReplayResult = { ...samples.replayResult };
    expect([status, findings, detail, dcaaDetail, summary, domains, disposition, evidence, rules, rule, config, accepted, rejected, runs, run, created, replay]).toHaveLength(17);
  });

  it('the DCAA additions of the contract are present in the samples (domains, domain fields, manifest additions)', () => {
    expect(samples.status.domains.map((d) => d.id)).toEqual(['labor', 'dcaa_cost_accounting', 'cmmc_evidence', 'proposals']);
    for (const f of samples.findingsList.items) {
      expect(typeof f.domain).toBe('string');
      expect(typeof f.domain_name).toBe('string');
    }
    for (const r of samples.rulesList.items) expect(typeof r.domain_name).toBe('string');
    expect(samples.findingDetailL11.domain).toBe('dcaa_cost_accounting');
    // computed is Record<string,string>: every value is a string, nothing structured reaches the UI
    for (const v of Object.values(samples.findingDetailL11.computed)) expect(typeof v).toBe('string');
    expect(samples.runDetail.manifest.enabled_domains).toContain('dcaa_cost_accounting');
    expect(samples.runDetail.manifest.inputs.map((i) => i.source)).toContain('rate_data');
    expect(samples.runDetail.manifest.reference_tables.map((t) => t.name)).toEqual(['account_categories', 'classification_history', 'account_allowability', 'ics_submissions']);
  });
});
