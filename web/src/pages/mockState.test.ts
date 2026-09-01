import { describe, expect, it } from 'vitest';
import {
  bodyForEditor,
  bodyIsInvalidJson,
  draftToAddArgs,
  draftToUpdateArgs,
  emptyDraft,
  exportFileContents,
  EXPORT_FILENAME,
  findRuleById,
  formatBody,
  hasTruncatedBodies,
  isSaveBlockedByTruncation,
  mergeExportBodies,
  normalizeMethod,
  patchRuleEnabled,
  ruleToDraft,
  type MockRule,
} from './mockState';

function rule(extras: Partial<MockRule> = {}): MockRule {
  return {
    id: 'mock-1',
    name: 'empty list',
    url_pattern: '/api/items',
    method: '*',
    match_type: 'contains',
    status_code: 200,
    response_headers: { 'content-type': 'application/json' },
    delay_ms: 0,
    enabled: true,
    hit_count: 3,
    ...extras,
  };
}

describe('normalizeMethod', () => {
  it('treats * and empty as all methods', () => {
    expect(normalizeMethod('*')).toBe('');
    expect(normalizeMethod('')).toBe('');
    expect(normalizeMethod(undefined)).toBe('');
  });

  it('uppercases a concrete method', () => {
    expect(normalizeMethod('get')).toBe('GET');
  });
});

describe('ruleToDraft / emptyDraft', () => {
  it('starts a new rule with add defaults', () => {
    const draft = emptyDraft();
    expect(draft.id).toBeNull();
    expect(draft.match_type).toBe('contains');
    expect(draft.status_code).toBe(200);
    expect(draft.enabled).toBe(true);
    expect(draft.method).toBe('');
    expect(draft.bodyTruncated).toBe(false);
    expect(isSaveBlockedByTruncation(draft)).toBe(false);
  });

  it('maps list * method to an empty form field', () => {
    expect(ruleToDraft(rule()).method).toBe('');
    expect(ruleToDraft(rule({ method: 'POST' })).method).toBe('POST');
  });

  it('prefers full response_body over preview', () => {
    const draft = ruleToDraft(
      rule({
        response_body: '{"ok":true}',
        response_body_preview: '{"ok":',
      }),
    );
    expect(draft.response_body).toBe('{"ok":true}');
  });

  it('falls back to preview when list omitted the body', () => {
    expect(ruleToDraft(rule({ response_body_preview: '{"ok":true}' })).response_body).toBe(
      '{"ok":true}',
    );
  });
});

describe('draft payloads', () => {
  it('add args include enabled and omit rule_id', () => {
    const args = draftToAddArgs({
      ...emptyDraft(),
      name: 'n',
      url_pattern: '/x',
      response_body: '{}',
    });
    expect(args.rule_id).toBeUndefined();
    expect(args.enabled).toBe(true);
    expect(args.name).toBe('n');
    expect(args.response_body).toBe('{}');
  });

  it('update args use rule_id and omit enabled', () => {
    const args = draftToUpdateArgs({
      ...emptyDraft(),
      id: 'mock-9',
      name: 'n',
    });
    expect(args.rule_id).toBe('mock-9');
    expect(args.enabled).toBeUndefined();
    expect(args.name).toBe('n');
  });
});

describe('mergeExportBodies', () => {
  it('fills missing bodies by list/export index when lengths match', () => {
    const merged = mergeExportBodies(
      [rule({ id: 'a' }), rule({ id: 'b', name: 'other', url_pattern: '/b' })],
      [
        { name: 'empty list', url_pattern: '/api/items', method: '', response_body: '{"a":1}' },
        { name: 'other', url_pattern: '/b', method: '', response_body: '{"b":2}' },
      ],
    );
    expect(merged[0]?.response_body).toBe('{"a":1}');
    expect(merged[1]?.response_body).toBe('{"b":2}');
  });

  it('keeps an existing full body', () => {
    const merged = mergeExportBodies(
      [rule({ response_body: '{"keep":1}' })],
      [{ name: 'empty list', url_pattern: '/api/items', response_body: '{"drop":1}' }],
    );
    expect(merged[0]?.response_body).toBe('{"keep":1}');
  });

  it('matches by name/pattern/method when lengths differ', () => {
    const merged = mergeExportBodies(
      [rule({ id: 'only' })],
      [
        { name: 'noise', url_pattern: '/nope', response_body: '{}' },
        { name: 'empty list', url_pattern: '/api/items', method: '*', response_body: '{"hit":1}' },
      ],
    );
    expect(merged[0]?.response_body).toBe('{"hit":1}');
  });
});

describe('formatBody / invalid json', () => {
  it('pretty-prints objects', () => {
    const formatted = formatBody('{"a":1}');
    expect(formatted.ok).toBe(true);
    expect(formatted.text).toContain('\n');
  });

  it('keeps invalid json and flags it', () => {
    expect(formatBody('not-json')).toEqual({ text: 'not-json', ok: false });
    expect(bodyIsInvalidJson('not-json')).toBe(true);
    expect(bodyIsInvalidJson('')).toBe(false);
    expect(bodyIsInvalidJson('{"a":1}')).toBe(false);
  });
});

describe('export / lookup / toggle patch', () => {
  it('writes mock-rules.json payload', () => {
    expect(EXPORT_FILENAME).toBe('mock-rules.json');
    expect(JSON.parse(exportFileContents([{ name: 'a' }]))).toEqual({
      rules: [{ name: 'a' }],
    });
  });

  it('finds a rule by id', () => {
    const rules = [rule({ id: 'mock-a' }), rule({ id: 'mock-b', name: 'b' })];
    expect(findRuleById(rules, 'mock-b')?.name).toBe('b');
    expect(findRuleById(rules, null)).toBeUndefined();
  });

  it('patches enabled in place', () => {
    const next = patchRuleEnabled([rule({ id: 'mock-1', enabled: true })], 'mock-1', false);
    expect(next[0]?.enabled).toBe(false);
  });
});

describe('bodyForEditor', () => {
  it('is truncated when list only has a preview', () => {
    expect(bodyForEditor({ response_body_preview: '{"ok":true}' })).toEqual({
      text: '{"ok":true}',
      truncated: true,
    });
  });

  it('is truncated when both full body and preview are missing', () => {
    expect(bodyForEditor({})).toEqual({ text: '', truncated: true });
  });

  it('is not truncated when response_body is present, including empty string', () => {
    expect(bodyForEditor({ response_body: '{}' }).truncated).toBe(false);
    expect(bodyForEditor({ response_body: '', response_body_preview: '{…}' })).toEqual({
      text: '',
      truncated: false,
    });
  });

  it('blocks save when the editor body is truncated', () => {
    const draft = ruleToDraft(rule({ response_body_preview: '{"ok":true}' }));
    expect(draft.bodyTruncated).toBe(true);
    expect(isSaveBlockedByTruncation(draft)).toBe(true);
    expect(hasTruncatedBodies([rule({ response_body_preview: '{…}' })])).toBe(true);
    expect(hasTruncatedBodies([rule({ response_body: '{}' })])).toBe(false);
  });
});

describe('mergeExportBodies truncation', () => {
  it('leaves rules truncated when export cannot fill the body', () => {
    const merged = mergeExportBodies(
      [rule({ id: 'a' })],
      [{ name: 'other', url_pattern: '/nope', response_body: '{}' }],
    );
    expect(bodyForEditor(merged[0]!).truncated).toBe(true);
    expect(hasTruncatedBodies(merged)).toBe(true);
  });
});
