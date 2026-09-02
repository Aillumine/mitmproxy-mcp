import { describe, expect, it } from 'vitest';
import type { TrafficRow } from '../poll/drainTraffic';
import {
  addPattern,
  filterTrafficByDisplayRules,
  hostGlobFromUrl,
  parseStoredTrafficFilter,
  passesDisplayFilter,
  removePattern,
  urlMatchesPattern,
} from './trafficFilter';

function row(url: string): TrafficRow {
  return {
    id: url,
    timestamp: 0,
    method: 'GET',
    url,
    domain: '',
    status: 200,
    type: 'XHR',
    size: 1,
    time: 1,
    error: null,
  };
}

describe('urlMatchesPattern', () => {
  it('matches host globs like *.flowgpt.com/*', () => {
    expect(
      urlMatchesPattern(
        'https://staging-mobile-backend.flowgpt.com/userchat/sync',
        '*.flowgpt.com/*',
      ),
    ).toBe(true);
    expect(
      urlMatchesPattern('https://image-cdn.flowgpt.com/a.webp', '*.flowgpt.com/*'),
    ).toBe(true);
    expect(urlMatchesPattern('https://google.com/', '*.flowgpt.com/*')).toBe(false);
  });

  it('treats patterns without * as substring', () => {
    expect(urlMatchesPattern('https://a.googleapis.com/x', 'googleapis.com')).toBe(true);
    expect(urlMatchesPattern('https://flowgpt.com/x', 'googleapis.com')).toBe(false);
  });
});

describe('passesDisplayFilter', () => {
  const api = row('https://api.flowgpt.com/v1');
  const ads = row('https://web.facebook.com/tr');

  it('shows all when disabled or empty', () => {
    expect(passesDisplayFilter(ads, { enabled: false, allow: ['*.flowgpt.com/*'], ignore: [] })).toBe(
      true,
    );
    expect(passesDisplayFilter(ads, { enabled: true, allow: [], ignore: [] })).toBe(true);
  });

  it('allow-list restricts; ignore-list hides even if allowed', () => {
    expect(
      passesDisplayFilter(api, {
        enabled: true,
        allow: ['*.flowgpt.com/*'],
        ignore: [],
      }),
    ).toBe(true);
    expect(
      passesDisplayFilter(ads, {
        enabled: true,
        allow: ['*.flowgpt.com/*'],
        ignore: [],
      }),
    ).toBe(false);
    expect(
      passesDisplayFilter(api, {
        enabled: true,
        allow: ['*.flowgpt.com/*'],
        ignore: ['*api.flowgpt.com*'],
      }),
    ).toBe(false);
  });
});

describe('filterTrafficByDisplayRules', () => {
  it('filters the list in place order', () => {
    const rows = [
      row('https://google.com/'),
      row('https://staging.flowgpt.com/a'),
      row('https://graph.facebook.com/x'),
    ];
    expect(
      filterTrafficByDisplayRules(rows, {
        enabled: true,
        allow: ['*.flowgpt.com/*'],
        ignore: [],
      }).map((item) => item.url),
    ).toEqual(['https://staging.flowgpt.com/a']);
  });
});

describe('hostGlobFromUrl', () => {
  it('builds a base-domain allow pattern from a group prefix', () => {
    expect(hostGlobFromUrl('https://staging-mobile-backend.flowgpt.com/')).toBe(
      '*.flowgpt.com/*',
    );
    expect(hostGlobFromUrl('https://image-cdn.flowopt.com/')).toBe('*.flowopt.com/*');
  });
});

describe('pattern list helpers', () => {
  it('dedupes adds and removes case-insensitively', () => {
    expect(addPattern(['*.FlowGPT.com/*'], '*.flowgpt.com/*')).toEqual(['*.FlowGPT.com/*']);
    expect(removePattern(['a', 'b'], 'A')).toEqual(['b']);
  });
});

describe('parseStoredTrafficFilter', () => {
  it('fills defaults for partial payloads', () => {
    expect(parseStoredTrafficFilter({ allow: [' *.flowgpt.com/* '] })).toEqual({
      enabled: true,
      allow: ['*.flowgpt.com/*'],
      ignore: [],
    });
  });
});
