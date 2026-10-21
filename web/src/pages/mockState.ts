import { prettyJson } from '../format';

export type MockType = 'http' | 'ws_message' | 'ws_server' | 'ws_fault';

export type MatchType = 'contains' | 'exact' | 'regex';

export type MockRule = {
  id: string;
  name: string;
  url_pattern: string;
  mock_type?: MockType;
  ws_config?: Record<string, unknown>;
  method: string;
  match_type: MatchType | string;
  status_code: number;
  response_headers?: Record<string, string>;
  response_body?: string;
  response_body_preview?: string;
  delay_ms: number;
  enabled: boolean;
  hit_count: number;
};

export type ExportRule = {
  name: string;
  url_pattern: string;
  mock_type?: MockType;
  ws_config?: Record<string, unknown>;
  method?: string;
  match_type?: string;
  status_code?: number;
  response_headers?: Record<string, string>;
  response_body: string;
  delay_ms?: number;
  enabled?: boolean;
};

export type MockDraft = {
  id: string | null;
  name: string;
  url_pattern: string;
  mock_type: MockType;
  ws_config_json: string;
  method: string;
  match_type: MatchType;
  status_code: number;
  delay_ms: number;
  enabled: boolean;
  response_body: string;
  response_headers: Record<string, string>;
  hit_count: number;
  bodyTruncated: boolean;
};

const MATCH_TYPES: MatchType[] = ['contains', 'exact', 'regex'];

export function normalizeMethod(method: string | undefined): string {
  if (!method || method === '*') return '';
  return method.toUpperCase();
}

export function asMatchType(value: string | undefined): MatchType {
  return MATCH_TYPES.includes(value as MatchType) ? (value as MatchType) : 'contains';
}

export function emptyDraft(): MockDraft {
  return {
    id: null,
    name: '',
    url_pattern: '',
    mock_type: 'http',
    ws_config_json: '{}',
    method: '',
    match_type: 'contains',
    status_code: 200,
    delay_ms: 0,
    enabled: true,
    response_body: '',
    response_headers: { 'content-type': 'application/json' },
    hit_count: 0,
    bodyTruncated: false,
  };
}

export function bodyForEditor(rule: Pick<MockRule, 'response_body' | 'response_body_preview'>): {
  text: string;
  truncated: boolean;
} {
  if (typeof rule.response_body === 'string') {
    return { text: rule.response_body, truncated: false };
  }
  return { text: rule.response_body_preview ?? '', truncated: true };
}

export function ruleToDraft(rule: MockRule): MockDraft {
  const body = bodyForEditor(rule);
  return {
    id: rule.id,
    name: rule.name,
    url_pattern: rule.url_pattern,
    mock_type: rule.mock_type ?? 'http',
    ws_config_json: JSON.stringify(rule.ws_config ?? {}, null, 2),
    method: normalizeMethod(rule.method),
    match_type: asMatchType(rule.match_type),
    status_code: rule.status_code,
    delay_ms: rule.delay_ms,
    enabled: rule.enabled,
    response_body: body.text,
    response_headers: rule.response_headers ?? {
      'content-type': 'application/json',
    },
    hit_count: rule.hit_count,
    bodyTruncated: body.truncated,
  };
}

export function hasTruncatedBodies(rules: MockRule[]): boolean {
  return rules.some((rule) => bodyForEditor(rule).truncated);
}

export function isSaveBlockedByTruncation(draft: Pick<MockDraft, 'bodyTruncated'>): boolean {
  return draft.bodyTruncated;
}

export function draftToAddArgs(draft: MockDraft): Record<string, unknown> {
  return {
    name: draft.name,
    url_pattern: draft.url_pattern,
    response_body: draft.response_body,
    mock_type: draft.mock_type,
    ws_config: JSON.parse(draft.ws_config_json),
    method: draft.method,
    match_type: draft.match_type,
    status_code: draft.status_code,
    delay_ms: draft.delay_ms,
    enabled: draft.enabled,
    response_headers: draft.response_headers,
  };
}

export function draftToUpdateArgs(draft: MockDraft): Record<string, unknown> {
  return {
    rule_id: draft.id,
    name: draft.name,
    url_pattern: draft.url_pattern,
    response_body: draft.response_body,
    mock_type: draft.mock_type,
    ws_config: JSON.parse(draft.ws_config_json),
    method: draft.method,
    match_type: draft.match_type,
    status_code: draft.status_code,
    delay_ms: draft.delay_ms,
    response_headers: draft.response_headers,
  };
}

export function sameRuleKey(
  a: {
    name: string;
    url_pattern: string;
    method?: string;
    mock_type?: MockType;
  },
  b: {
    name: string;
    url_pattern: string;
    method?: string;
    mock_type?: MockType;
  },
): boolean {
  return (
    a.name === b.name &&
    a.url_pattern === b.url_pattern &&
    (a.mock_type ?? 'http') === (b.mock_type ?? 'http') &&
    normalizeMethod(a.method) === normalizeMethod(b.method)
  );
}

export function mergeExportBodies(list: MockRule[], exported: ExportRule[]): MockRule[] {
  return list.map((rule, index) => {
    if (typeof rule.response_body === 'string') return rule;
    const byIndex = exported[index];
    if (
      list.length === exported.length &&
      byIndex &&
      typeof byIndex.response_body === 'string' &&
      sameRuleKey(rule, byIndex)
    ) {
      return { ...rule, response_body: byIndex.response_body };
    }
    const match = exported.find((item) => sameRuleKey(rule, item));
    return match ? { ...rule, response_body: match.response_body } : rule;
  });
}

export function formatBody(raw: string): { text: string; ok: boolean } {
  const parsed = prettyJson(raw);
  return { text: parsed.text, ok: parsed.ok };
}

export function bodyIsInvalidJson(raw: string): boolean {
  if (!raw.trim()) return false;
  return !prettyJson(raw).ok;
}

export const EXPORT_FILENAME = 'mock-rules.json';

export function exportFileContents(rules: unknown[]): string {
  return JSON.stringify({ rules }, null, 2);
}

export function findRuleById(
  rules: MockRule[],
  id: string | null | undefined,
): MockRule | undefined {
  if (!id) return undefined;
  return rules.find((rule) => rule.id === id);
}

export function patchRuleEnabled(rules: MockRule[], id: string, enabled: boolean): MockRule[] {
  return rules.map((rule) => (rule.id === id ? { ...rule, enabled } : rule));
}

export function wsConfigTemplate(type: MockType): string {
  // New fault rules reproduce a mid-stream pause; legacy rules still default to HTTP.
  //
  // 新故障规则默认复现流式过程中暂停，已有规则仍按原来的 HTTP 默认值处理。
  return JSON.stringify(
    type === 'ws_fault'
      ? {
          direction: 'server',
          action: 'pause',
          trigger: 'messages',
          after_messages: 5,
          after_ms: 10000,
          duration_ms: 10000,
          jitter_ms: 0,
          probability: 1,
          preserve_heartbeat: true,
          close_code: 1000,
          close_reason: 'mock fault',
        }
      : type === 'ws_message'
        ? {
            direction: 'server',
            action: 'replace',
            message_pattern: '',
            message_match_type: 'contains',
          }
        : type === 'ws_server'
          ? {
              protocol: 'raw',
              messages: [{ body: '{"hello":"mock"}', delay_ms: 0 }],
              ping_interval_ms: 25000,
              ping_timeout_ms: 20000,
            }
          : {},
    null,
    2,
  );
}

export function wsConfigError(raw: string): string | null {
  try {
    const value: unknown = JSON.parse(raw);
    return value !== null && typeof value === 'object' && !Array.isArray(value)
      ? null
      : 'WebSocket 配置必须是 JSON 对象';
  } catch {
    return 'WebSocket 配置 JSON 格式有误';
  }
}
