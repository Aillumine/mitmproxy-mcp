import { describe, expect, it } from 'vitest';
import {
  drainTraffic,
  mergeTrafficPage,
  PACKAGE_CURSOR_LAG_SECONDS,
  shouldRebuildFromFirstPage,
  type TrafficRow,
} from './drainTraffic';

function row(id: string, timestamp: number): TrafficRow {
  return {
    id,
    timestamp,
    method: 'GET',
    url: `https://example.com/${id}`,
    domain: 'example.com',
    status: 200,
    type: 'XHR',
    size: 1,
    time: 1,
    error: null,
  };
}

describe('mergeTrafficPage', () => {
  it('sorts by timestamp ascending and uniques by id', () => {
    const existing = [row('c', 3), row('a', 1)];
    const page = [row('b', 2), row('a', 1)];
    expect(mergeTrafficPage(existing, page).map((item) => item.id)).toEqual([
      'a',
      'b',
      'c',
    ]);
  });

  it('lets the incoming page replace an existing id', () => {
    const existing = [{ ...row('a', 1), status: 200 }];
    const page = [{ ...row('a', 1), status: 500 }];
    const merged = mergeTrafficPage(existing, page);
    expect(merged).toHaveLength(1);
    expect(merged[0]?.status).toBe(500);
  });
});

describe('drainTraffic', () => {
  it('first page reverses newest-first into chronological and does not paginate history', async () => {
    const calls: Array<{ after_id?: string; offset: number }> = [];
    const result = await drainTraffic({
      afterId: null,
      fetchPage: async ({ after_id, offset }) => {
        calls.push({ after_id, offset });
        return {
          success: true,
          requests: [row('c', 3), row('b', 2), row('a', 1)],
          returned: 3,
        };
      },
    });
    expect(calls).toEqual([{ after_id: undefined, offset: 0 }]);
    expect(result.items.map((item) => item.id)).toEqual(['a', 'b', 'c']);
    expect(result.newestId).toBe('c');
  });

  it('incremental keeps after_id fixed and uses offset so middle records are not skipped', async () => {
    const calls: Array<{ after_id?: string; offset: number }> = [];
    const result = await drainTraffic({
      afterId: 'anchor',
      fetchPage: async ({ after_id, offset }) => {
        calls.push({ after_id, offset });
        if (offset === 0) {
          return {
            success: true,
            requests: Array.from({ length: 10 }, (_, i) =>
              row(`n${19 - i}`, 100 + (19 - i)),
            ),
            returned: 10,
          };
        }
        return {
          success: true,
          requests: Array.from({ length: 5 }, (_, i) =>
            row(`n${9 - i}`, 100 + (9 - i)),
          ),
          returned: 5,
        };
      },
    });
    expect(calls).toEqual([
      { after_id: 'anchor', offset: 0 },
      { after_id: 'anchor', offset: 10 },
    ]);
    expect(result.items.map((item) => item.id)).toEqual(
      Array.from({ length: 15 }, (_, i) => `n${5 + i}`),
    );
    expect(result.newestId).toBe('n19');
  });

  it('stops when returned < 10', async () => {
    const calls: Array<{ offset: number }> = [];
    const result = await drainTraffic({
      afterId: 'anchor',
      fetchPage: async ({ offset }) => {
        calls.push({ offset });
        return {
          success: true,
          requests: Array.from({ length: 10 }, (_, i) =>
            row(`n${9 - i}`, 10 + (9 - i)),
          ),
          returned: 5,
        };
      },
    });
    expect(calls).toEqual([{ offset: 0 }]);
    expect(result.items.map((item) => item.id)).toEqual(
      Array.from({ length: 10 }, (_, i) => `n${i}`),
    );
    expect(result.newestId).toBe('n9');
  });

  it('empty incremental keeps previous newestId', async () => {
    const result = await drainTraffic({
      afterId: 'anchor',
      fetchPage: async () => ({ success: true, requests: [], returned: 0 }),
    });
    expect(result.items).toEqual([]);
    expect(result.newestId).toBe('anchor');
  });
});

describe('shouldRebuildFromFirstPage', () => {
  it('rebuilds a new WSS group after clear when the cursor id was reused', () => {
    expect(
      shouldRebuildFromFirstPage({
        afterId: 'ws-4',
        incrementalItems: 0,
        visibleRows: 0,
      }),
    ).toBe(true);
  });

  it('does not rebuild while waiting for the next frame on a populated list', () => {
    expect(
      shouldRebuildFromFirstPage({
        afterId: 'ws-4',
        incrementalItems: 0,
        visibleRows: 4,
      }),
    ).toBe(false);
  });

  it('does not rebuild the first live page that has no cursor yet', () => {
    expect(
      shouldRebuildFromFirstPage({
        afterId: null,
        incrementalItems: 0,
        visibleRows: 0,
      }),
    ).toBe(false);
  });
});

// A stand-in for the backend's traffic table plus the two writers that race on
// it: the addon inserts rows with package = null, the attribution sampler tags
// them ~1s later. fetchPage mirrors sqlite_store.query(): after_id resolves to
// that row's timestamp and the filter is a strict `timestamp > ?`, newest first.
//
// 模拟后端的 traffic 表和抢着写它的两方：addon 插入的行 package 是 null，
// 归属采样器约 1 秒后才给它打标。fetchPage 照搬 sqlite_store.query()：
// after_id 先换成该行的时间戳，再用严格的 `timestamp > ?` 过滤，按时间倒序。
function fakeBackend() {
  const stored: TrafficRow[] = [];
  return {
    insert(id: string, timestamp: number) {
      stored.push({ ...row(id, timestamp), package: null });
    },
    backfill(pkg: string, ids: string[]) {
      for (const item of stored) {
        if (ids.includes(item.id) && item.package == null) item.package = pkg;
      }
    },
    fetchPage: async ({
      after_id,
      limit,
      offset,
    }: {
      after_id?: string;
      limit: number;
      offset: number;
    }) => {
      const cursor = stored.find((item) => item.id === after_id);
      const visible = stored
        .filter((item) => (cursor ? item.timestamp > cursor.timestamp : true))
        .sort((a, b) => b.timestamp - a.timestamp)
        .slice(offset, offset + limit)
        // Copies, like a real fetch — a later backfill must not mutate rows the
        // client already holds.
        //
        // 和真实请求一样返回副本——之后的回填不能直接改到客户端已经拿到的行。
        .map((item) => ({ ...item }));
      return { success: true, requests: visible, returned: visible.length };
    },
  };
}

describe('package attribution arrives after the row was already drained', () => {
  async function pollTwice(lagSeconds: number) {
    const backend = fakeBackend();
    // An old row, so the first drain has something to park the cursor on.
    //
    // 先放一行老记录，第一次 drain 才有地方停游标。
    backend.insert('old', 100);
    backend.insert('req-1', 200);

    let cursor: string | null = null;
    let rows: TrafficRow[] = [];

    const tick = async () => {
      const result = await drainTraffic({
        afterId: cursor,
        lagSeconds,
        fetchPage: backend.fetchPage,
      });
      rows = mergeTrafficPage(rows, result.items);
      if (result.items.length > 0 && result.newestId) cursor = result.newestId;
    };

    // Tick 1 wins the race: req-1 is read before the sampler has tagged it.
    //
    // 第一拍抢先：req-1 在采样器给它打标之前就被读走了。
    await tick();
    expect(rows.find((item) => item.id === 'req-1')?.package).toBeNull();

    // The sampler catches up a beat later, as it always does.
    //
    // 采样器慢一拍才跟上——它每次都是这样。
    backend.backfill('com.example.app', ['req-1']);

    await tick();
    return rows;
  }

  it('re-delivers the row so the package reaches the client', async () => {
    const rows = await pollTwice(PACKAGE_CURSOR_LAG_SECONDS);
    expect(rows.find((item) => item.id === 'req-1')?.package).toBe(
      'com.example.app',
    );
  });

  it('loses the package forever when the cursor jumps to the newest row', async () => {
    // The pre-fix behaviour, kept as the counter-example: with the cursor pinned
    // to the newest row, traffic_list never returns req-1 again and the browser
    // is stuck with the null it read first — about half the requests vanish from
    // a package-filtered list, at random.
    //
    // 修复前的行为，留作反例：游标紧贴最新一行时，traffic_list 再也不会返回
    // req-1，浏览器就永远停在第一次读到的 null 上——按包名过滤的列表会随机
    // 丢掉大约一半的请求。
    const rows = await pollTwice(0);
    expect(rows.find((item) => item.id === 'req-1')?.package).toBeNull();
  });
});

describe('laggedCursor via drainTraffic', () => {
  it('parks the cursor on the newest row older than the lag window', async () => {
    const result = await drainTraffic({
      afterId: 'seed',
      lagSeconds: 5,
      fetchPage: async () => ({
        success: true,
        requests: [row('c', 110), row('b', 104), row('a', 100)],
        returned: 3,
      }),
    });
    expect(result.newestId).toBe('b');
  });

  it('keeps the old cursor when every drained row is still inside the window', async () => {
    const result = await drainTraffic({
      afterId: 'seed',
      lagSeconds: 5,
      fetchPage: async () => ({
        success: true,
        requests: [row('b', 101), row('a', 100)],
        returned: 2,
      }),
    });
    expect(result.newestId).toBe('seed');
  });
});
