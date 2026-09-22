// One small icon set, drawn on a 16px grid with no dependencies. Every status gets a DISTINCT SHAPE (triangle, diamond,
// dot, cross, check, dashed ring), so meaning never rests on colour alone. Icons are decorative: the visible text label
// carries the meaning, so each SVG is hidden from assistive technology.
import type { ReactNode } from 'react';

export interface IconProps {
  size?: number;
  className?: string;
}

function icon(children: ReactNode, opts: { stroke?: number } = {}) {
  return function Icon({ size = 16, className }: IconProps) {
    return (
      <svg
        viewBox="0 0 16 16"
        width={size}
        height={size}
        fill="none"
        stroke="currentColor"
        strokeWidth={opts.stroke ?? 1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
        className={className}
      >
        {children}
      </svg>
    );
  };
}

const WHITE = '#fff';

export const Icons = {
  // brand and navigation
  Tick: icon(<path d="M3.4 8.6l3.1 3.1 6.1-7.2" />, { stroke: 2.4 }),
  Pulse: icon(<path d="M1.5 8.2h3l2-5 3 9.6 2-4.6h3" />),
  List: icon(
    <>
      <path d="M6.2 4h8M6.2 8h8M6.2 12h8" />
      <circle cx="2.6" cy="4" r=".6" fill="currentColor" />
      <circle cx="2.6" cy="8" r=".6" fill="currentColor" />
      <circle cx="2.6" cy="12" r=".6" fill="currentColor" />
    </>,
  ),
  Sliders: icon(
    <>
      <path d="M2 4h6.2M12 4h2M2 12h2.2M8 12h6" />
      <circle cx="10.1" cy="4" r="1.9" />
      <circle cx="6.1" cy="12" r="1.9" />
    </>,
  ),
  History: icon(
    <>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.6V8l2.3 1.5" />
    </>,
  ),

  // severity: a triangle, a diamond and a dot
  Triangle: icon(
    <>
      <path d="M8 1.8 15 13.8H1z" fill="currentColor" stroke="none" />
      <path d="M8 6.2v3.4" stroke={WHITE} strokeWidth="1.5" />
      <circle cx="8" cy="11.6" r=".85" fill={WHITE} stroke="none" />
    </>,
  ),
  Diamond: icon(<path d="M8 1.6 14.4 8 8 14.4 1.6 8z" fill="currentColor" stroke="none" />),
  Dot: icon(<circle cx="8" cy="8" r="5" fill="currentColor" stroke="none" />),

  // results: a cross, an outlined triangle, a check and a dashed ring
  XCircle: icon(
    <>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8" />
    </>,
  ),
  TriangleOutline: icon(
    <>
      <path d="M8 2.6 14 13.2H2z" />
      <path d="M8 6.6v3M8 11.6v.01" />
    </>,
  ),
  CheckCircle: icon(
    <>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M5.2 8.2l1.9 1.9 3.7-4" />
    </>,
  ),
  DashedCircle: icon(<circle cx="8" cy="8" r="6.2" strokeDasharray="2.3 2.3" />),

  // finding status: a square, a half circle, a check, a diamond-check, a cross, a bar
  Square: icon(<rect x="3" y="3" width="10" height="10" rx="2.2" fill="currentColor" stroke="none" />),
  HalfCircle: icon(
    <>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M8 1.8a6.2 6.2 0 0 1 0 12.4z" fill="currentColor" stroke="none" />
    </>,
  ),
  CheckFilled: icon(
    <>
      <circle cx="8" cy="8" r="6.6" fill="currentColor" stroke="none" />
      <path d="M5.2 8.2l1.9 1.9 3.7-4" stroke={WHITE} />
    </>,
  ),
  DiamondCheck: icon(
    <>
      <path d="M8 1.8 14.2 8 8 14.2 1.8 8z" />
      <path d="M5.6 8.1l1.7 1.7 3.1-3.3" />
    </>,
  ),
  SquareCross: icon(
    <>
      <rect x="2.6" y="2.6" width="10.8" height="10.8" rx="2.4" />
      <path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" />
    </>,
  ),
  CircleBar: icon(
    <>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M5.2 8h5.6" />
    </>,
  ),

  // states and feedback
  Info: icon(
    <>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M8 7.4v3.6M8 5.1v.01" />
    </>,
  ),
  Alert: icon(
    <>
      <path d="M8 2.2 14.3 13.4H1.7z" />
      <path d="M8 6.4v3.2M8 11.5v.01" />
    </>,
  ),
  Clock: icon(
    <>
      <circle cx="8" cy="8" r="6.2" />
      <path d="M8 4.6V8l2.3 1.5" />
    </>,
  ),
  Refresh: icon(
    <>
      <path d="M13.2 8a5.2 5.2 0 1 1-1.7-3.8" />
      <path d="M13.4 2.6v3h-3" />
    </>,
  ),
};
