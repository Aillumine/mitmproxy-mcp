import { prettyJson } from '../format';

export type MatchType = 'contains' | 'exact' | 'regex';

export type MockRule = {
  id: string;
  name: string;
  url_pattern: string;
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
    method: normalizeMethod(rule.method),
    match_type: asMatchType(rule.match_type),
    status_code: rule.status_code,
    delay_ms: rule.delay_ms,
    enabled: rule.enabled,
    response_body: body.text,
    response_headers: rule.response_headers ?? { 'content-type': 'application/json' },
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
    method: draft.method,
    match_type: draft.match_type,
    status_code: draft.status_code,
    delay_ms: draft.delay_ms,
    response_headers: draft.response_headers,
  };
}

export function sameRuleKey(
  a: { name: string; url_pattern: string; method?: string },
  b: { name: string; url_pattern: string; method?: string },
): boolean {
  return (
    a.name === b.name &&
    a.url_pattern === b.url_pattern &&
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

export function findRuleById(rules: MockRule[], id: string | null | undefined): MockRule | undefined {
  if (!id) return undefined;
  return rules.find((rule) => rule.id === id);
}

export function patchRuleEnabled(rules: MockRule[], id: string, enabled: boolean): MockRule[] {
  return rules.map((rule) => (rule.id === id ? { ...rule, enabled } : rule));
}
