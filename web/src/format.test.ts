import { describe, expect, it } from 'vitest';
import {
  BODY_LIMIT,
  bodyChunkCodePointLength,
  looksTruncated,
  prettyJson,
  prettyLooseJson,
  urlAtOffset,
} from './format';

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

describe('bodyChunkCodePointLength', () => {
  it('prefers the server length field', () => {
    expect(bodyChunkCodePointLength({ length: 1, content: '🎀' })).toBe(1);
  });

  it('counts emoji as one code point when length is omitted', () => {
    // JS '🎀'.length === 2; code-point length must stay 1 to match Python slicing.
    expect('🎀'.length).toBe(2);
    expect(bodyChunkCodePointLength({ content: '🎀hi' })).toBe(3);
  });
});

describe('urlAtOffset', () => {
  const line = '  "avatar": "https://cdn.example.com/a.webp",';

  it('finds the url under the cursor', () => {
    expect(urlAtOffset(line, 20)).toBe('https://cdn.example.com/a.webp');
  });

  it('returns null outside the url', () => {
    expect(urlAtOffset(line, 4)).toBeNull();
  });

  it('stops at the closing quote', () => {
    expect(urlAtOffset('"http://a.co/x" "http://b.co/y"', 5)).toBe('http://a.co/x');
  });

  it('picks the second url on the line', () => {
    expect(urlAtOffset('"http://a.co/x" "http://b.co/y"', 20)).toBe('http://b.co/y');
  });

  it('drops trailing punctuation', () => {
    expect(urlAtOffset('see https://a.co/x.', 10)).toBe('https://a.co/x');
  });
});
