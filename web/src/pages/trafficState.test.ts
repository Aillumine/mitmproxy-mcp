import { describe, expect, it } from 'vitest';
import type { TrafficRow } from '../poll/drainTraffic';
import {
  apiPath,
  buildCurlCommand,
  countTrafficByKind,
  filterTrafficByKind,
  groupTrafficByPrefix,
  isHttpsConnectTunnel,
  isSelectedInRows,
  looksLikeHtml,
  looksLikeImage,
  originPrefix,
  parseQueryPairs,
  patchRowFromDetail,
  resolveCopyText,
  trafficKind,
} from './trafficState';

function row(id: string, extras: Partial<TrafficRow> = {}): TrafficRow {
  return {
    id,
    timestamp: 0,
    method: 'GET',
    url: `https://example.com/${id}`,
    domain: 'example.com',
    status: 0,
    type: '',
    size: 0,
    time: 0,
    error: null,
    ...extras,
  };
}

describe('isHttpsConnectTunnel', () => {
  it('hides CONNECT tunnel rows with empty bodies', () => {
    expect(
      isHttpsConnectTunnel(
        row('c1', { method: 'CONNECT', url: 'https://api.example.com:443/', status: 0, size: 0 }),
      ),
    ).toBe(true);
  });

  it('keeps TLS handshake failures', () => {
    expect(
      isHttpsConnectTunnel(
        row('tls', {
          method: 'CONNECT',
          type: 'TLS',
          error: 'certificate verify failed',
          url: 'https://api.example.com',
        }),
      ),
    ).toBe(false);
  });

  it('keeps ordinary HTTP requests', () => {
    expect(isHttpsConnectTunnel(row('get'))).toBe(false);
  });
});

describe('isSelectedInRows', () => {
  it('is false when the selected id is missing after filter/search replace', () => {
    expect(isSelectedInRows([row('a'), row('b')], 'gone')).toBe(false);
  });

  it('is true when the selected id is still present', () => {
    expect(isSelectedInRows([row('a'), row('b')], 'b')).toBe(true);
  });

  it('is false when nothing is selected', () => {
    expect(isSelectedInRows([row('a')], null)).toBe(false);
  });
});

describe('patchRowFromDetail', () => {
  it('backfills status/time/type/size/url in place and keeps table order', () => {
    const stub = row('req-1', { status: 0, time: 0, size: 12, timestamp: 0 });
    const later = row('req-2', { timestamp: 1 });
    const patched = patchRowFromDetail([stub, later], {
      id: 'req-1',
      timestamp: 99,
      method: 'POST',
      url: 'https://example.com/patched',
      domain: 'example.com',
      status: 201,
      resource_type: 'XHR',
      response_size: 44,
      time_ms: 18.7,
      error: null,
    });
    expect(patched.map((item) => item.id)).toEqual(['req-1', 'req-2']);
    expect(patched[0]?.timestamp).toBe(0);
    expect(patched[0]?.status).toBe(201);
    expect(patched[0]?.time).toBe(18.7);
    expect(patched[0]?.type).toBe('XHR');
    expect(patched[0]?.size).toBe(44);
    expect(patched[0]?.url).toBe('https://example.com/patched');
  });
});

describe('buildCurlCommand', () => {
  it('quotes the URL and omits -X for GET without extras', () => {
    expect(
      buildCurlCommand({ method: 'GET', url: 'https://example.com/api?q=1' }),
    ).toBe("curl 'https://example.com/api?q=1'");
  });

  it('adds method, headers, and body; skips hop-by-hop headers', () => {
    expect(
      buildCurlCommand({
        method: 'POST',
        url: "https://example.com/it's",
        headers: {
          Host: 'example.com',
          'Content-Type': 'application/json',
          'Content-Length': '2',
          Connection: 'keep-alive',
          Authorization: 'Bearer tok',
        },
        body: `{"a":"b'c"}`,
      }),
    ).toBe(
      "curl -X POST 'https://example.com/it'\\''s' -H 'Content-Type: application/json' -H 'Authorization: Bearer tok' --data-raw '{\"a\":\"b'\\''c\"}'",
    );
  });
});

describe('parseQueryPairs', () => {
  it('reads query names from the URL', () => {
    expect(
      parseQueryPairs('https://example.com/userchat/sync?type=all&take=200&cursor=next'),
    ).toEqual([
      { name: 'type', value: 'all' },
      { name: 'take', value: '200' },
      { name: 'cursor', value: 'next' },
    ]);
  });

  it('is empty when the URL has no query', () => {
    expect(parseQueryPairs('https://example.com/userchat/sync')).toEqual([]);
  });
});

describe('originPrefix', () => {
  it('groups https APIs by origin with a trailing slash', () => {
    expect(
      originPrefix('https://staging-mobile-backend.flowgpt.com/prompt/search/hot-words'),
    ).toBe('https://staging-mobile-backend.flowgpt.com/');
  });

  it('keeps wss origins separate from https', () => {
    expect(originPrefix('wss://staging-mobile-backend.flowgpt.com/ws')).toBe(
      'wss://staging-mobile-backend.flowgpt.com/',
    );
  });

  it('falls back for unparseable URLs', () => {
    expect(originPrefix('not a url')).toBe('other');
  });

  it('puts Socket.IO handshakes into the wss group even when stored as https', () => {
    expect(
      originPrefix(
        'https://staging-ws-flow-dev.flowgpt.com/socket.io/?EIO=4&transport=websocket',
        'WebSocket',
      ),
    ).toBe('wss://staging-ws-flow-dev.flowgpt.com/');
  });
});

describe('apiPath', () => {
  it('copies the path without origin or query', () => {
    expect(
      apiPath('https://staging-mobile-backend.flowgpt.com/prompt/v2/filter?lang=en'),
    ).toBe('/prompt/v2/filter');
  });
});

describe('groupTrafficByPrefix', () => {
  it('groups https and wss prefixes, keeping first-seen group order', () => {
    const grouped = groupTrafficByPrefix([
      row('a', { url: 'https://staging-mobile-backend.flowgpt.com/a', timestamp: 1 }),
      row('ws', { url: 'wss://staging-mobile-backend.flowgpt.com/ws', timestamp: 2 }),
      row('b', { url: 'https://staging-mobile-backend.flowgpt.com/b', timestamp: 3 }),
      row('other', { url: 'https://cdn.example.com/x', timestamp: 4 }),
    ]);
    expect(grouped.map((group) => group.prefix)).toEqual([
      'https://staging-mobile-backend.flowgpt.com/',
      'wss://staging-mobile-backend.flowgpt.com/',
      'https://cdn.example.com/',
    ]);
    expect(grouped[0]?.rows.map((item) => item.id)).toEqual(['a', 'b']);
    expect(grouped[1]?.rows.map((item) => item.id)).toEqual(['ws']);
  });
});

describe('resolveCopyText', () => {
  const input = {
    method: 'POST',
    url: 'https://api.example.com/v1/user?lang=en',
    requestHeaders: { Authorization: 'Bearer tok', 'Content-Type': 'application/json' },
    requestBody: '{"name":"ada"}',
    responseHeaders: { 'Content-Type': 'application/json' },
    responseBody: '{"id":1}',
  };

  it('builds curl, api path, headers, and pretty JSON params', () => {
    expect(resolveCopyText('url', input)).toBe('https://api.example.com/v1/user?lang=en');
    expect(resolveCopyText('api', input)).toBe('/v1/user');
    expect(resolveCopyText('reqHeaders', input)).toBe(
      'Authorization: Bearer tok\nContent-Type: application/json',
    );
    expect(resolveCopyText('reqParams', input)).toBe('lang=en\n\n{\n  "name": "ada"\n}');
    expect(resolveCopyText('resHeaders', input)).toBe('Content-Type: application/json');
    expect(resolveCopyText('resParams', input)).toBe('{\n  "id": 1\n}');
    expect(resolveCopyText('curl', input)).toContain("curl -X POST 'https://api.example.com/v1/user?lang=en'");
  });

  it('copies query only when there is no body', () => {
    expect(
      resolveCopyText('reqParams', {
        method: 'GET',
        url: 'https://api.example.com/sync?type=all&take=200',
      }),
    ).toBe('type=all&take=200');
  });

  it('pretty-prints Socket.IO frames when copying response params', () => {
    expect(
      resolveCopyText('resParams', {
        method: 'WS',
        url: 'wss://example.com/socket.io/?EIO=4&transport=websocket',
        responseBody: '42["chatStream",{"sequence":16}]',
      }),
    ).toBe('42[\n  "chatStream",\n  {\n    "sequence": 16\n  }\n]');
  });
});

describe('trafficKind', () => {
  it('puts API calls in HTTP and upgrades in Socket', () => {
    expect(trafficKind(row('api', { type: 'XHR' }))).toBe('http');
    expect(
      trafficKind(
        row('ws', {
          method: 'WS',
          type: 'WebSocket',
          url: 'wss://staging-ws.example.com/socket.io/?EIO=4&transport=websocket',
        }),
      ),
    ).toBe('socket');
  });

  it('maps document/script/style/image/media types', () => {
    expect(
      trafficKind(
        row('html', {
          type: 'Document',
          url: 'https://translate.google.com/m?client=gtx',
        }),
      ),
    ).toBe('html');
    expect(trafficKind(row('js', { type: 'Script', url: 'https://cdn.example.com/app.js' }))).toBe(
      'js',
    );
    expect(
      trafficKind(row('css', { type: 'Stylesheet', url: 'https://cdn.example.com/app.css' })),
    ).toBe('css');
    expect(trafficKind(row('img', { type: 'Image', url: 'https://cdn.example.com/a.png' }))).toBe(
      'image',
    );
    expect(trafficKind(row('vid', { type: 'Media', url: 'https://cdn.example.com/a.mp4' }))).toBe(
      'media',
    );
  });

  it('filters and counts by kind', () => {
    const rows = [
      row('api', { type: 'XHR' }),
      row('page', { type: 'Document', url: 'https://example.com/m' }),
      row('ws', { method: 'WS', type: 'WebSocket', url: 'wss://example.com/ws' }),
    ];
    expect(filterTrafficByKind(rows, 'html').map((item) => item.id)).toEqual(['page']);
    expect(countTrafficByKind(rows)).toMatchObject({
      all: 3,
      http: 1,
      html: 1,
      socket: 1,
    });
  });
});

describe('looksLikeHtml', () => {
  it('detects HTML fragments and content-type', () => {
    expect(looksLikeHtml('<p>hello</p>')).toBe(true);
    expect(looksLikeHtml('<!DOCTYPE html><html></html>')).toBe(true);
    expect(looksLikeHtml('{"ok":true}')).toBe(false);
    expect(looksLikeHtml('42["chatStream",{}]')).toBe(false);
    expect(looksLikeHtml('<p>x</p>', 'text/html; charset=utf-8')).toBe(true);
    expect(looksLikeHtml('<div/>', undefined, 'Document')).toBe(true);
  });
});

describe('looksLikeImage', () => {
  it('detects image by resource type, content-type, or extension', () => {
    expect(
      looksLikeImage(
        'https://image-cdn.flowopt.com/trans-images/a.webp',
        undefined,
        'Image',
      ),
    ).toBe(true);
    expect(
      looksLikeImage('https://cdn.example.com/x', 'image/webp; charset=binary'),
    ).toBe(true);
    expect(looksLikeImage('https://cdn.example.com/a.png')).toBe(true);
    expect(looksLikeImage('https://api.example.com/v1/user', 'application/json')).toBe(
      false,
    );
  });
});
