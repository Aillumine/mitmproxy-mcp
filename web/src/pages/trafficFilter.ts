import type { TrafficRow } from '../poll/drainTraffic';

export const TRAFFIC_FILTER_STORAGE_KEY = 'mitm.trafficDisplayFilter';

export type TrafficDisplayFilter = {
  enabled: boolean;
  /** 非空时：只显示命中任一规则的流量（Charles Focus / Proxyman Allow） */
  allow: string[];
  /** 命中则从列表隐藏（Proxyman Hide but not Block） */
  ignore: string[];
};

export const EMPTY_TRAFFIC_FILTER: TrafficDisplayFilter = {
  enabled: true,
  allow: [],
  ignore: [],
};

/** 把 `*.flowgpt.com/*` 这类 glob 编成正则；无 `*` 时按子串包含。 */
export function compileUrlPattern(pattern: string): RegExp | null {
  const raw = pattern.trim();
  if (!raw) return null;
  if (!raw.includes('*')) {
    const escaped = raw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp(escaped, 'i');
  }
  const parts = raw.split('*').map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  // `*.example.com` must also cover the apex `example.com`: hostGlobFromUrl
  // turns `https://cloudflare-dns.com/` into `*.cloudflare-dns.com/*`, which
  // previously required a dot before the host and so never matched its own row.
  // The label boundary (`.` or the `//` after the scheme) keeps `evilexample.com` out.
  //
  // `*.example.com` 也要命中裸域 `example.com`：一键生成的 `*.cloudflare-dns.com/*`
  // 之前要求 host 前必须有 `.`，导致连生成它的那一行都匹配不上。
  // 要求前面是 `.` 或 scheme 后的 `/`，避免误伤 `evilexample.com`。
  const body = parts.join('.*').replace(/\.\*\\\./g, '(?:.*[/.])?');
  return new RegExp(`^${body}$`, 'i');
}

export function urlMatchesPattern(url: string, pattern: string): boolean {
  const re = compileUrlPattern(pattern);
  if (!re) return false;
  // TLS handshake failures are recorded as `https://host` with no path, so a
  // host glob like `*.example.com/*` never matched them and those rows could
  // not be hidden. Give a path-less URL its root `/` before testing.
  //
  // TLS 握手失败的行记录为 `https://host`，不带路径，导致 `*.example.com/*`
  // 这类 host 规则匹配不上、无法忽略（如 captive.samsungconnectivity.com）。
  // 没有路径的 URL 先补上根路径 `/` 再匹配。
  return re.test(/^[a-z][a-z0-9+.-]*:\/\/[^/]*$/i.test(url) ? `${url}/` : url);
}

export function urlMatchesAny(url: string, patterns: string[]): boolean {
  return patterns.some((pattern) => urlMatchesPattern(url, pattern));
}

export function passesDisplayFilter(
  row: Pick<TrafficRow, 'url'>,
  filter: TrafficDisplayFilter,
): boolean {
  if (!filter.enabled) return true;
  const url = row.url || '';
  if (filter.ignore.length > 0 && urlMatchesAny(url, filter.ignore)) return false;
  if (filter.allow.length > 0 && !urlMatchesAny(url, filter.allow)) return false;
  return true;
}

export function filterTrafficByDisplayRules(
  rows: TrafficRow[],
  filter: TrafficDisplayFilter,
): TrafficRow[] {
  if (!filter.enabled) return rows;
  if (filter.allow.length === 0 && filter.ignore.length === 0) return rows;
  return rows.filter((row) => passesDisplayFilter(row, filter));
}

/** 从分组前缀或完整 URL 生成可加入规则的 host glob，如 `*.flowgpt.com/*`。 */
export function hostGlobFromUrl(urlOrPrefix: string): string {
  try {
    const normalized = /:\/\/$/.test(urlOrPrefix)
      ? urlOrPrefix
      : urlOrPrefix.endsWith('/')
        ? urlOrPrefix
        : `${urlOrPrefix}/`;
    const parsed = new URL(normalized.startsWith('http') ? normalized : `https://${normalized}`);
    const host = parsed.hostname;
    const parts = host.split('.').filter(Boolean);
    if (parts.length >= 2) {
      const base = parts.slice(-2).join('.');
      return `*.${base}/*`;
    }
    return `*${host}*`;
  } catch {
    const host = urlOrPrefix.replace(/^https?:\/\//i, '').split('/')[0] ?? urlOrPrefix;
    return host ? `*${host}*` : urlOrPrefix;
  }
}

export function normalizePatternList(patterns: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of patterns) {
    const value = item.trim();
    if (!value) continue;
    const key = value.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }
  return out;
}

export function addPattern(list: string[], pattern: string): string[] {
  return normalizePatternList([...list, pattern]);
}

export function removePattern(list: string[], pattern: string): string[] {
  const want = pattern.trim().toLowerCase();
  return list.filter((item) => item.trim().toLowerCase() !== want);
}

// A host in both lists reads as broken: ignore wins in passesDisplayFilter, so
// the row disappears while "只显示 (1)" still looks active. Keep the two lists
// mutually exclusive — the latest click decides which side a pattern lives on.
//
// 同一个 host 同时出现在两个列表里会显得像坏了：passesDisplayFilter 里 ignore
// 优先，请求被隐藏，可界面上「只显示 (1)」还亮着。所以两个列表互斥，
// 以最后一次点击为准。
export function applyPattern(
  filter: TrafficDisplayFilter,
  side: 'allow' | 'ignore',
  pattern: string,
): TrafficDisplayFilter {
  return side === 'allow'
    ? {
        ...filter,
        allow: addPattern(filter.allow, pattern),
        ignore: removePattern(filter.ignore, pattern),
      }
    : {
        ...filter,
        allow: removePattern(filter.allow, pattern),
        ignore: addPattern(filter.ignore, pattern),
      };
}

export function parseStoredTrafficFilter(raw: unknown): TrafficDisplayFilter {
  if (!raw || typeof raw !== 'object') return { ...EMPTY_TRAFFIC_FILTER };
  const data = raw as Record<string, unknown>;
  return {
    enabled: data.enabled !== false,
    allow: Array.isArray(data.allow)
      ? normalizePatternList(data.allow.filter((item): item is string => typeof item === 'string'))
      : [],
    ignore: Array.isArray(data.ignore)
      ? normalizePatternList(data.ignore.filter((item): item is string => typeof item === 'string'))
      : [],
  };
}

export function loadTrafficFilter(
  storage: Pick<Storage, 'getItem'> | null = typeof localStorage !== 'undefined' ? localStorage : null,
): TrafficDisplayFilter {
  if (!storage) return { ...EMPTY_TRAFFIC_FILTER };
  try {
    const raw = storage.getItem(TRAFFIC_FILTER_STORAGE_KEY);
    if (!raw) return { ...EMPTY_TRAFFIC_FILTER };
    return parseStoredTrafficFilter(JSON.parse(raw));
  } catch {
    return { ...EMPTY_TRAFFIC_FILTER };
  }
}

export function saveTrafficFilter(
  filter: TrafficDisplayFilter,
  storage: Pick<Storage, 'setItem'> | null = typeof localStorage !== 'undefined' ? localStorage : null,
): void {
  if (!storage) return;
  try {
    storage.setItem(TRAFFIC_FILTER_STORAGE_KEY, JSON.stringify(filter));
  } catch {
    // ignore quota / private mode
  }
}

export function activeFilterCount(filter: TrafficDisplayFilter): number {
  if (!filter.enabled) return 0;
  return filter.allow.length + filter.ignore.length;
}
