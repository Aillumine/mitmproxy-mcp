export const BODY_LIMIT = 1_000_000;

export type PrettyJson =
  | { ok: true; text: string; value: unknown; prefix: string }
  | { ok: false; text: string };

/**
 * traffic_read_body 的 offset/length 按 Unicode 码点计数（与 Python len 一致）。
 * 不能用 JS 的 string.length：emoji 等非 BMP 字符在 JS 里占 2 个 UTF-16 unit，
 * 会导致分片错位、JSON 损坏，从而无法格式化。
 */
export function bodyChunkCodePointLength(chunk: {
  length?: number;
  content?: string;
}): number {
  if (typeof chunk.length === 'number') return chunk.length;
  return [...(chunk.content ?? '')].length;
}

function indentJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function parsePrefixedJson(raw: string): { prefix: string; value: unknown } | null {
  for (let i = 0; i < raw.length; i += 1) {
    const ch = raw[i];
    if (ch !== '{' && ch !== '[') continue;
    try {
      return { prefix: raw.slice(0, i), value: JSON.parse(raw.slice(i)) };
    } catch {
      // this brace is not a complete JSON value; try the next one
    }
  }
  return null;
}

export function prettyJson(raw: string): PrettyJson {
  try {
    const value = JSON.parse(raw);
    return { ok: true, text: indentJson(value), value, prefix: '' };
  } catch {
    return { ok: false, text: raw };
  }
}

/** Socket.IO / Engine.IO: keep `42` prefix, indent the JSON payload. */
export function prettyLooseJson(raw: string): PrettyJson {
  const strict = prettyJson(raw);
  if (strict.ok) return strict;
  const embedded = parsePrefixedJson(raw);
  if (!embedded) return { ok: false, text: raw };
  return {
    ok: true,
    text: embedded.prefix + indentJson(embedded.value),
    value: embedded.value,
    prefix: embedded.prefix,
  };
}

export function looksTruncated(hasMore: boolean, length: number): boolean {
  return hasMore || length >= BODY_LIMIT;
}
