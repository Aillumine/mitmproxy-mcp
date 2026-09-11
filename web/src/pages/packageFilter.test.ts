import { describe, expect, it } from 'vitest';
import type { TrafficRow } from '../poll/drainTraffic';
import { filterRowsByPackage, parsePackages } from './packageFilter';

function row(id: string, pkg: string | null): TrafficRow {
  return {
    id,
    timestamp: 0,
    method: 'GET',
    url: 'https://example.com/',
    domain: 'example.com',
    status: 200,
    type: 'XHR',
    size: 0,
    time: 0,
    error: null,
    package: pkg,
  } as TrafficRow;
}

describe('filterRowsByPackage', () => {
  const rows = [row('a', 'com.example.app'), row('b', 'com.other'), row('c', null)];

  it('returns everything when no package is selected', () => {
    expect(filterRowsByPackage(rows, null)).toHaveLength(3);
  });

  it('keeps only the selected package', () => {
    expect(filterRowsByPackage(rows, 'com.example.app').map((r) => r.id)).toEqual(['a']);
  });

  it('hides rows that have not been attributed yet', () => {
    expect(filterRowsByPackage(rows, 'com.example.app').map((r) => r.id)).not.toContain('c');
  });
});

describe('parsePackages', () => {
  it('reads the tool envelope', () => {
    const parsed = parsePackages({
      packages: [
        { package: 'com.a', uid: 10234, foreground: true },
        { package: 'com.b', uid: 10666, foreground: false },
      ],
    });
    expect(parsed).toHaveLength(2);
    expect(parsed[0]).toEqual({
      packageName: 'com.a',
      uid: 10234,
      foreground: true,
    });
  });

  it('survives a malformed response', () => {
    expect(parsePackages({})).toEqual([]);
    expect(parsePackages({ packages: 'nope' as unknown as [] })).toEqual([]);
  });
});
