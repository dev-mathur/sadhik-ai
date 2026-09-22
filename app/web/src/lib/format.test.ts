import { describe, expect, it } from 'vitest';
import { formatDateTime, formatUsd, humanize, ratioToPercent } from './format';

describe('formatting works on text and never converts figures to numbers', () => {
  it('formats money with grouping and the API decimals', () => {
    expect(formatUsd('3504.00')).toBe('$3,504.00');
    expect(formatUsd('41298.93')).toBe('$41,298.93');
    expect(formatUsd('1986240.00')).toBe('$1,986,240.00');
    expect(formatUsd('0.00')).toBe('$0.00');
    expect(formatUsd('12')).toBe('$12');
  });
  it('keeps every digit of a value a float could not represent', () => {
    expect(formatUsd('12345678901234567890.10')).toBe('$12,345,678,901,234,567,890.10');
  });
  it('returns unrecognised input unchanged and a dash for missing values', () => {
    expect(formatUsd('n/a')).toBe('n/a');
    expect(formatUsd(null)).toBe('—');
  });
  it('formats timestamps without timezone conversion', () => {
    expect(formatDateTime('2026-09-03T14:22:07Z')).toBe('2026-09-03 14:22 UTC');
    expect(formatDateTime(null)).toBe('—');
  });
  it('turns a ratio into a meter width by moving the decimal point, never by multiplying', () => {
    expect(ratioToPercent('0.8571')).toBe('85.71%');
    expect(ratioToPercent('1.0000')).toBe('100%');
    expect(ratioToPercent('0.85')).toBe('85%');
    expect(ratioToPercent('0.05')).toBe('5%');
    expect(ratioToPercent('0')).toBe('0%');
    expect(ratioToPercent('0.0000')).toBe('0%');
    expect(ratioToPercent('0.123456')).toBe('12.3456%'); // no float rounding noise: a float would give 12.3456...01
    expect(ratioToPercent('n/a')).toBe('0%');
    expect(ratioToPercent(null)).toBe('0%');
  });
  it('humanizes enum values', () => {
    expect(humanize('semi_monthly')).toBe('Semi monthly');
    expect(humanize('lower_is_stricter')).toBe('Lower is stricter');
  });
});
