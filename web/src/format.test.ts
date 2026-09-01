import { describe, expect, it } from 'vitest';
import { BODY_LIMIT, looksTruncated, prettyJson, prettyLooseJson } from './format';

describe('prettyJson', () => {
  it('indents objects', () => {
    const r = prettyJson('{"a":1}');
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.text).toContain('\n');
      expect(r.text).toMatch(/{\s*\n\s+"a":\s*1\s*\n}/);
      expect(r.value).toEqual({ a: 1 });
    }
  });
  it('returns original on invalid json', () => {
    const r = prettyJson('not-json');
    expect(r.ok).toBe(false);
    expect(r.text).toBe('not-json');
  });

  it('does not treat Socket.IO frames as strict JSON', () => {
    const r = prettyJson('42["chatStream",{"id":1}]');
    expect(r.ok).toBe(false);
  });
});

describe('prettyLooseJson', () => {
  it('indents Socket.IO event frames and keeps the packet prefix', () => {
    const r = prettyLooseJson('42["chatStream",{"chunk":"hi","sequence":16}]');
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.prefix).toBe('42');
    expect(r.text).toBe(
      '42[\n  "chatStream",\n  {\n    "chunk": "hi",\n    "sequence": 16\n  }\n]',
    );
    expect(r.value).toEqual(['chatStream', { chunk: 'hi', sequence: 16 }]);
  });

  it('indents Engine.IO OPEN payloads', () => {
    const r = prettyLooseJson('0{"sid":"abc","pingInterval":25000}');
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.prefix).toBe('0');
    expect(r.text).toContain('\n  "sid": "abc"');
  });

  it('leaves non-JSON packets as-is', () => {
    const r = prettyLooseJson('2probe');
    expect(r.ok).toBe(false);
    expect(r.text).toBe('2probe');
  });
});

describe('looksTruncated', () => {
  it('is true when the server still has more', () => {
    expect(looksTruncated(true, 10)).toBe(true);
  });
  it('is true when length hits BODY_LIMIT', () => {
    expect(looksTruncated(false, BODY_LIMIT)).toBe(true);
  });
  it('is false when complete and under the limit', () => {
    expect(looksTruncated(false, BODY_LIMIT - 1)).toBe(false);
  });
});
