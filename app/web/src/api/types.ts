// Types derived from contracts/sample_payloads/*.json (binding contract: contracts/api.md).
// Money, hours and ratios are STRINGS. The UI formats them for display and never does arithmetic on them.
// Enumerations are typed as string on purpose: the UI maps known values to labels and shows unknown values verbatim.

export type Money = string;

export interface ApiErrorBody {
  error: { code: string; message: string };
}

// ---- status.json -----------------------------------------------------------------------------
export interface TierState {
  tier: string;
  label: string;
  cadence: string;
  last_run_id: string | null;
  last_run_at: string | null;
  rules_in_tier: number;
  rules_evaluated: number;
  state: string; // current | overdue | never_run
  next_due: string | null;
  note: string | null;
}

export interface Coverage {
  evaluated: number;
  applicable: number;
  ratio: string;
  floor: string;
}

/** One compliance domain on the status page (contracts/api.md, DCAA additions 1-2). */
export interface DomainState {
  id: string; // labor | dcaa_cost_accounting | cmmc_evidence | proposals (unknown ids are shown verbatim)
  name: string;
  enabled: boolean;
  available: boolean; // false: a placeholder that is never enabled
  state: string; // "8 open findings" | "Incomplete data" | "Not enabled" | "coming later", shown as sent
  label_kind: string; // open_findings | no_open_findings | incomplete_data | unavailable (only guaranteed for enabled domains)
  open_findings: number;
  by_severity: Record<string, number>;
  coverage: Coverage | null; // null unless the domain is enabled
}

export interface Status {
  period: string;
  as_of: string;
  label: string;
  label_kind: string; // open_findings | no_open_findings | incomplete_data
  open_findings: number;
  by_severity: Record<string, number>;
  total_exposure_usd: Money;
  coverage: Coverage;
  last_run_id: string | null;
  last_run_at: string | null;
  tiers: TierState[];
  oldest_source: { source: string; file: string; data_as_of: string; age_days: number } | null;
  domains: DomainState[];
  rules: { rule_id: string; result: string; domain: string; reason?: string }[];
}

// ---- findings_list.json ----------------------------------------------------------------------
export interface FindingSummary {
  finding_id: string;
  rule_id: string;
  headline: string;
  severity: string;
  status: string;
  exposure_usd: Money;
  employee_count: number;
  employees: string[];
  contracts: string[];
  period: string;
  domain: string;
  domain_name: string;
}

export interface FindingsList {
  total: number;
  items: FindingSummary[];
}

// ---- finding_detail.json / disposition_response.json ------------------------------------------
export interface HistoryRow {
  from: string | null;
  to: string;
  actor: string;
  at: string;
  reason_code: string | null;
  note: string | null;
}

export interface FindingDetail {
  finding_id: string;
  fingerprint: string;
  run_id: string;
  latest_run_id: string;
  rule_id: string;
  rule_version: string;
  period: string;
  headline: string;
  authorities: string[];
  basis: string;
  severity: string;
  severity_reason: string;
  status: string;
  metric: {
    id: string;
    value: string;
    numerator: string;
    denominator: string;
    threshold_tripped: string;
    baseline_confidence: string;
  } | null;
  computed: Record<string, string>;
  exposure_usd: Money;
  exposure_basis: string;
  affected: { employees: string[]; contracts: string[]; entries: number };
  explanation: {
    what_happened: string;
    why_it_matters: string;
    impact: string;
    recommended_action: string;
  };
  evidence_count: number;
  created_at: string;
  history: HistoryRow[];
  allowed_transitions: string[];
  domain: string;
  domain_name: string;
}

export interface DispositionRequest {
  disposition: string;
  reason_code?: string;
  note?: string;
  actor: string;
}

// ---- evidence.json ---------------------------------------------------------------------------
export interface EvidenceItem {
  kind: string;
  ref_id: string;
  source_file: string;
  sha256: string;
  row: number | null;
  detail: Record<string, string | number | boolean | null | undefined>;
}

export interface EvidencePage {
  finding_id: string;
  total: number;
  page: number;
  page_size: number;
  items: EvidenceItem[];
}

// ---- rules_list.json / rule_detail.json ------------------------------------------------------
export interface RuleParameter {
  name: string;
  default: string;
  effective: string;
  direction: string;
  min: string | null;
  max: string | null;
  floor: string | null;
  unit: string;
  description: string;
}

export interface Rule {
  id: string;
  version: string;
  title: string;
  authorities: string[];
  basis: string;
  tier: string;
  required_sources: string[];
  review_status: string;
  status: string;
  last_result: string;
  parameters: RuleParameter[];
  domain: string;
  domain_name: string;
}

export interface RulesList {
  floor_registry_version: string;
  items: Rule[];
}

// ---- config_get.json / config_accepted.json / config_rejected.json ----------------------------
export interface ConfigHistoryRow {
  config_version: number;
  changed_by: string;
  approved_by: string | null;
  reason: string;
  effective_from: string;
  sha256: string;
}

export interface ConfigGet {
  config_version: number;
  sha256: string;
  yaml: string;
  floor_registry_version: string;
  history: ConfigHistoryRow[];
}

export interface ConfigError {
  line: number | null; // 1-based; null when the error cannot be attributed to a line (contract clarification 3)
  path: string;
  code: string;
  message: string;
}

export interface ConfigResult {
  accepted: boolean;
  errors: ConfigError[];
  warnings: string[];
  sha256: string | null;
  config_version: number | null;
}

// ---- runs_list.json / run_detail.json / run_created.json / replay_result.json ---------------
export interface RunSummary {
  run_id: string;
  period: string;
  tier: string;
  executed_at: string;
  findings: number;
  total_exposure_usd: Money;
  label: string;
  config_version: number;
}

export interface RunsList {
  items: RunSummary[];
}

export interface ManifestInput {
  source: string;
  file: string;
  sha256: string;
  rows: number;
  data_as_of: string;
}

export interface ReferenceTable {
  name: string; // e.g. account_categories | classification_history
  sha256: string;
}

export interface RunManifest {
  run_id: string;
  period: string;
  tier: string;
  executed_at: string;
  inputs: ManifestInput[];
  sources_absent: string[];
  mapping_versions: Record<string, number>;
  rule_versions: Record<string, string>;
  config_file: { config_version: number; sha256: string };
  floor_registry_version: string;
  declared_schedule: Record<string, string>;
  materiality: { basis_usd: Money; pct: string; usd: Money };
  enabled_domains: string[];
  reference_tables: ReferenceTable[];
}

export interface RunRuleResult {
  rule_id: string;
  domain?: string; // present in the sample, not listed in the contract text
  result: string;
  findings: number;
  reason?: string;
  metrics: { id: string; value: string; result: string }[];
}

export interface RunDetail {
  run_id: string;
  period: string;
  tier: string;
  executed_at: string;
  manifest: RunManifest;
  rules: RunRuleResult[];
  total_exposure_usd: Money;
  label: string;
  findings: string[];
}

export interface RunCreated {
  run_id: string;
  period: string;
  tier: string;
  executed_at: string;
  findings: number;
  new_findings: number;
  total_exposure_usd: Money;
  label: string;
}

export interface ReplayResult {
  run_id: string;
  identical: boolean;
  findings_compared: number;
  diff: unknown[]; // element shape is not specified by the contract (sample has an empty array)
  reason: string | null;
}
