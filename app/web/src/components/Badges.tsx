import type { ComponentType } from 'react';
import { Icons, type IconProps } from '../lib/icons';
import { SEVERITY_LABEL, STATUS_LABEL, TIER_STATE } from '../lib/format';
import { Glyph } from './Glyph';

// Every badge carries an icon AND a text label, and each state has its own SHAPE: status is never conveyed by colour alone.
type IconType = ComponentType<IconProps>;

const SEVERITY_ICON: Record<string, IconType> = { critical: Icons.Triangle, high: Icons.Triangle, medium: Icons.Diamond, low: Icons.Dot };
const STATUS_ICON: Record<string, IconType> = {
  open: Icons.Square,
  in_review: Icons.HalfCircle,
  confirmed: Icons.CheckFilled,
  legit_exception: Icons.DiamondCheck,
  data_error: Icons.SquareCross,
  remediated: Icons.CheckCircle,
  closed: Icons.CircleBar,
};
const RESULT_ICON: Record<string, IconType> = {
  Exception: Icons.XCircle,
  Watch: Icons.TriangleOutline,
  Consistent: Icons.CheckCircle,
  'Not evaluated': Icons.DashedCircle,
};
const RESULT_TONE: Record<string, string> = {
  Exception: 'res-exception',
  Watch: 'res-watch',
  Consistent: 'res-ok',
  'Not evaluated': 'res-none',
};
const TIER_ICON: Record<string, IconType> = { ok: Icons.CheckCircle, warn: Icons.Clock, muted: Icons.DashedCircle };

export function SeverityBadge({ severity }: { severity: string }) {
  const known = severity in SEVERITY_LABEL;
  return (
    <span className={`badge sev-${known ? severity : 'unknown'}`}>
      <Glyph icon={SEVERITY_ICON[severity] ?? Icons.Info} />
      <span>{SEVERITY_LABEL[severity] ?? severity}</span>
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge status-${status}`}>
      <Glyph icon={STATUS_ICON[status] ?? Icons.Info} />
      <span>{STATUS_LABEL[status] ?? status}</span>
    </span>
  );
}

export function ResultBadge({ result }: { result: string }) {
  return (
    <span className={`badge ${RESULT_TONE[result] ?? 'res-none'}`}>
      <Glyph icon={RESULT_ICON[result] ?? Icons.DashedCircle} />
      <span>{result}</span>
    </span>
  );
}

export function TierStateBadge({ state }: { state: string }) {
  const s = TIER_STATE[state] ?? { label: state, glyph: '', tone: 'muted' };
  return (
    <span className={`badge tier-${s.tone}`}>
      <Glyph icon={TIER_ICON[s.tone] ?? Icons.Info} />
      <span>{s.label}</span>
    </span>
  );
}
