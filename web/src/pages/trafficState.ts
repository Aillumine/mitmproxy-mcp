import { prettyLooseJson } from '../format';
import type { TrafficRow } from '../poll/drainTraffic';

export type DetailFields = {
  id: string;
  timestamp: number;
  method: string;
  url: string;
  domain: string;
  status: number;
  resource_type: string;
  response_size: number;
  time_ms: number;
  error: string | null;
};

export function applyDetailListFields(row: TrafficRow, req: DetailFields): TrafficRow {
  return {
    ...row,
    status: req.status,
    time: req.time_ms,
    type: req.resource_type,
    size: req.response_size,
    url: req.url,
  };
}

export function patchRowFromDetail(rows: TrafficRow[], req: DetailFields): TrafficRow[] {
  return rows.map((row) => (row.id === req.id ? applyDetailListFields(row, req) : row));
}

export function parseQueryPairs(url: string): { name: string; value: string }[] {
  try {
    const parsed = new URL(url);
    return [...parsed.searchParams.entries()].map(([name, value]) => ({ name, value }));
  } catch {
    return [];
  }
}

export function isHttpsConnectTunnel(
  row: Pick<TrafficRow, 'method' | 'type' | 'error'>,
): boolean {
  return row.method.toUpperCase() === 'CONNECT' && row.type !== 'TLS' && !row.error;
}

export function withoutConnectTunnels(rows: TrafficRow[]): TrafficRow[] {
  return rows.filter((row) => !isHttpsConnectTunnel(row));
}

export function isSelectedInRows(
  rows: TrafficRow[],
  selectedId: string | null,
): boolean {
  return selectedId != null && rows.some((row) => row.id === selectedId);
}

const SKIP_CURL_HEADERS = new Set([
  'host',
  'content-length',
  'transfer-encoding',
  'connection',
  'keep-alive',
  'proxy-connection',
  'upgrade',
  'te',
  'trailer',
]);

export function shellQuote(value: string): string {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

export function buildCurlCommand(opts: {
  method: string;
  url: string;
  headers?: Record<string, string>;
  body?: string;
}): string {
  const parts = ['curl'];
  const method = (opts.method || 'GET').toUpperCase();
  if (method && method !== 'GET') {
    parts.push('-X', method);
  }
  parts.push(shellQuote(opts.url || ''));
  for (const [name, value] of Object.entries(opts.headers ?? {})) {
    if (SKIP_CURL_HEADERS.has(name.toLowerCase())) continue;
    parts.push('-H', shellQuote(`${name}: ${value}`));
  }
  if (opts.body) {
    parts.push('--data-raw', shellQuote(opts.body));
  }
  return parts.join(' ');
}

export type TrafficGroup = {
  prefix: string;
  rows: TrafficRow[];
};

export function asWebsocketUrl(url: string, type?: string): string {
  const looksWs =
    type === 'WebSocket' ||
    /(?:^|[?&])transport=websocket(?:&|$)/i.test(url) ||
    url.startsWith('wss:') ||
    url.startsWith('ws:');
  if (!looksWs) return url;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:') parsed.protocol = 'wss:';
    else if (parsed.protocol === 'http:') parsed.protocol = 'ws:';
    return parsed.toString();
  } catch {
    return url;
  }
}

export function originPrefix(url: string, type?: string): string {
  try {
    const parsed = new URL(asWebsocketUrl(url, type));
    return `${parsed.protocol}//${parsed.host}/`;
  } catch {
    return 'other';
  }
}

export function apiPath(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname || '/';
  } catch {
    return url;
  }
}

export const TRAFFIC_KINDS = [
  { id: 'all', label: '全部' },
  { id: 'http', label: 'HTTP' },
  { id: 'socket', label: 'Socket' },
  { id: 'html', label: 'HTML' },
  { id: 'js', label: 'JS' },
  { id: 'css', label: 'CSS' },
  { id: 'image', label: 'Image' },
  { id: 'media', label: 'Media' },
] as const;

export type TrafficKind = (typeof TRAFFIC_KINDS)[number]['id'];
export type TrafficKindId = Exclude<TrafficKind, 'all'>;

const IMAGE_EXT = new Set([
  '.jpg',
  '.jpeg',
  '.png',
  '.gif',
  '.svg',
  '.webp',
  '.ico',
  '.bmp',
]);
const MEDIA_EXT = new Set([
  '.mp4',
  '.mp3',
  '.webm',
  '.ogg',
  '.wav',
  '.m4a',
  '.avi',
  '.mov',
]);
const SCRIPT_EXT = new Set(['.js', '.mjs', '.cjs', '.jsx']);

function urlExtension(url: string): string {
  try {
    const path = new URL(url).pathname;
    const dot = path.lastIndexOf('.');
    if (dot < 0) return '';
    const ext = path.slice(dot).toLowerCase();
    return ext.length <= 6 ? ext : '';
  } catch {
    return '';
  }
}

export function trafficKind(
  row: Pick<TrafficRow, 'method' | 'url' | 'type'>,
): TrafficKindId {
  const type = row.type || '';
  const url = row.url || '';
  if (
    row.method.toUpperCase() === 'WS' ||
    type === 'WebSocket' ||
    /^wss?:/i.test(url) ||
    /(?:^|[?&])transport=websocket(?:&|$)/i.test(url)
  ) {
    return 'socket';
  }
  const ext = urlExtension(url);
  if (type === 'Image' || IMAGE_EXT.has(ext)) return 'image';
  if (type === 'Media' || MEDIA_EXT.has(ext)) return 'media';
  if (type === 'Stylesheet' || ext === '.css') return 'css';
  if (type === 'Script' || SCRIPT_EXT.has(ext)) return 'js';
  if (type === 'Document' || ext === '.html' || ext === '.htm') return 'html';
  return 'http';
}

export function filterTrafficByKind(
  rows: TrafficRow[],
  kind: TrafficKind,
): TrafficRow[] {
  if (kind === 'all') return rows;
  return rows.filter((row) => trafficKind(row) === kind);
}

export function countTrafficByKind(rows: TrafficRow[]): Record<TrafficKind, number> {
  const counts: Record<TrafficKind, number> = {
    all: rows.length,
    http: 0,
    socket: 0,
    html: 0,
    js: 0,
    css: 0,
    image: 0,
    media: 0,
  };
  for (const row of rows) {
    counts[trafficKind(row)] += 1;
  }
  return counts;
}

export function looksLikeHtml(
  raw: string,
  contentType?: string,
  resourceType?: string,
): boolean {
  if (resourceType === 'Document') return Boolean(raw.trim());
  const mime = (contentType ?? '').split(';')[0]?.trim().toLowerCase() ?? '';
  if (mime.includes('html')) return Boolean(raw.trim());
  const text = raw.trimStart();
  if (!text.startsWith('<')) return false;
  return /^(?:<!doctype\s+html|<!--|<html[\s>]|<[a-z][\w:.-]*(?:[\s/>]|$))/i.test(
    text,
  );
}

export function groupTrafficByPrefix(rows: TrafficRow[]): TrafficGroup[] {
  const order: string[] = [];
  const buckets = new Map<string, TrafficRow[]>();
  for (const item of rows) {
    const prefix = originPrefix(item.url, item.type);
    const bucket = buckets.get(prefix);
    if (bucket) {
      bucket.push(item);
    } else {
      order.push(prefix);
      buckets.set(prefix, [item]);
    }
  }
  return order.map((prefix) => ({ prefix, rows: buckets.get(prefix) ?? [] }));
}

export const COPY_MENU_ITEMS = [
  { id: 'curl', label: 'cURL' },
  { id: 'api', label: '接口名称' },
  { id: 'reqHeaders', label: '请求头' },
  { id: 'reqParams', label: '请求参数' },
  { id: 'resHeaders', label: '响应头' },
  { id: 'resParams', label: '响应参数' },
] as const;

export type CopyKind = (typeof COPY_MENU_ITEMS)[number]['id'];

export type CopyPayload = {
  method: string;
  url: string;
  requestHeaders?: Record<string, string>;
  requestBody?: string;
  responseHeaders?: Record<string, string>;
  responseBody?: string;
};

export function prettyMaybeJson(raw: string): string {
  const text = raw.trim();
  if (!text) return '';
  const parsed = prettyLooseJson(text);
  return parsed.ok ? parsed.text : raw;
}

export function formatHeadersForCopy(headers?: Record<string, string>): string {
  if (!headers) return '';
  return Object.entries(headers)
    .map(([name, value]) => `${name}: ${value}`)
    .join('\n');
}

export function formatRequestParamsForCopy(url: string, body?: string): string {
  let query = '';
  try {
    query = new URL(url).search.replace(/^\?/, '');
  } catch {
    query = '';
  }
  const bodyText = prettyMaybeJson(body ?? '');
  if (query && bodyText) return `${query}\n\n${bodyText}`;
  return bodyText || query;
}

export function resolveCopyText(kind: CopyKind, input: CopyPayload): string {
  switch (kind) {
    case 'curl':
      return buildCurlCommand({
        method: input.method,
        url: input.url,
        headers: input.requestHeaders,
        body: input.requestBody,
      });
    case 'api':
      return apiPath(input.url);
    case 'reqHeaders':
      return formatHeadersForCopy(input.requestHeaders);
    case 'reqParams':
      return formatRequestParamsForCopy(input.url, input.requestBody);
    case 'resHeaders':
      return formatHeadersForCopy(input.responseHeaders);
    case 'resParams':
      return prettyMaybeJson(input.responseBody ?? '');
  }
}
