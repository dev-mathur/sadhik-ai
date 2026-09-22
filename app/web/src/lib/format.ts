// Display formatting only. Money, hours and ratios arrive as strings and are never converted to numbers:
// every helper here works on the text so no float ever touches a figure.

/** "3504.00" -> "$3,504.00". Text-only: groups digits, keeps the API's decimals. Unrecognised input is returned as-is. */
export function formatUsd(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const m = /^(-?)(\d+)(\.\d+)?$/.exec(value.trim());
  if (!m) return value;
  const [, sign, whole, frac = ''] = m;
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${sign}$${grouped}${frac}`;
}

/** "2026-09-03T14:22:07Z" -> "2026-09-03 14:22 UTC". Text-only, no timezone conversion. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z)?$/.exec(iso);
  if (!m) return iso;
  return `${m[1]} ${m[2]}${m[3] ? ' UTC' : ''}`;
}

/**
 * "0.8571" -> "85.71%", "1.0000" -> "100%". Text-only, like formatUsd: it shifts the decimal point by string, so no
 * arithmetic touches a figure. Used ONLY for the width of a meter; the number itself is always shown as the API sent it.
 * Anything unrecognised gives "0%".
 */
export function ratioToPercent(value: string | null | undefined): string {
  const m = /^(\d)(?:\.(\d+))?$/.exec((value ?? '').trim());
  if (!m) return '0%';
  const frac = (m[2] ?? '').padEnd(2, '0');
  const whole = (m[1] + frac.slice(0, 2)).replace(/^0+(?=\d)/, '');
  const rest = frac.slice(2).replace(/0+$/, '');
  return `${whole}${rest ? '.' + rest : ''}%`;
}

export function dash(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === '' ? '—' : String(value);
}

/** "semi_monthly" -> "Semi monthly"; "lower_is_stricter" -> "Lower is stricter". */
export function humanize(value: string | null | undefined): string {
  if (!value) return '—';
  const s = value.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

export const SEVERITY_LABEL: Record<string, string> = { high: 'High', medium: 'Medium', low: 'Low' };
export const SEVERITY_GLYPH: Record<string, string> = { high: '▲', medium: '◆', low: '●' };
export const SEVERITY_ORDER = ['high', 'medium', 'low'];

export const STATUS_LABEL: Record<string, string> = {
  open: 'Open',
  in_review: 'In review',
  confirmed: 'Confirmed',
  legit_exception: 'Legitimate exception',
  data_error: 'Data error',
  remediated: 'Remediated',
  closed: 'Closed',
};
export const STATUS_ORDER = ['open', 'in_review', 'confirmed', 'legit_exception', 'data_error', 'remediated', 'closed'];

/** Button text for moving a finding to a state. Keys are the API's `allowed_transitions` values. */
export const TRANSITION_LABEL: Record<string, string> = {
  open: 'Reopen',
  in_review: 'Move to in review',
  confirmed: 'Confirm finding',
  legit_exception: 'Mark legitimate exception',
  data_error: 'Mark data error',
  remediated: 'Mark remediated',
  closed: 'Close finding',
};

export const REASON_CODES: { code: string; label: string }[] = [
  { code: 'documented_correction', label: 'Documented correction' },
  { code: 'approved_exception', label: 'Approved exception' },
  { code: 'source_data_error', label: 'Source data error' },
  { code: 'policy_change', label: 'Policy change' },
  { code: 'other', label: 'Other' },
];

export const TIERS: { value: string; label: string }[] = [
  { value: 'fast', label: 'Fast' },
  { value: 'pay_period', label: 'Pay period' },
  { value: 'close', label: 'Close' },
];

export const RESULT_GLYPH: Record<string, string> = {
  Exception: '✖',
  Watch: '▲',
  Consistent: '✔',
  'Not evaluated': '○',
};

export const TIER_STATE: Record<string, { label: string; glyph: string; tone: string }> = {
  current: { label: 'Current', glyph: '✔', tone: 'ok' },
  overdue: { label: 'Overdue', glyph: '⚠', tone: 'warn' },
  never_run: { label: 'Never run', glyph: '○', tone: 'muted' },
};
