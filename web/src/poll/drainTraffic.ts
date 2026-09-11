export type TrafficRow = {
  id: string;
  timestamp: number;
  method: string;
  url: string;
  domain: string;
  status: number;
  type: string;
  size: number;
  time: number;
  error: string | null;
  package?: string | null;
};

export type TrafficListPage = {
  success: boolean;
  requests?: TrafficRow[];
  returned?: number;
};

const PAGE_SIZE = 10;

// How far behind the newest row the incremental cursor is parked while a package
// filter is active. A row is written with package = NULL and only tagged by the
// next attribution sample (~1s later), so a cursor pinned to the newest row would
// leave every row the poll happened to win the race against untagged forever —
// traffic_list only ever returns rows newer than the cursor. Parking the cursor
// behind the tag latency re-delivers each row a few times until its package has
// landed; the re-sent volume is bounded by this window.
//
// 启用包名过滤时，增量游标要落在最新一行之后多久。行落库时 package 是 NULL，
// 要等下一轮归属采样（约 1 秒后）才打标；游标若紧贴最新行，凡是被轮询抢先拿到
// 的行就永远停在未归属状态——traffic_list 只会返回比游标更新的行。把游标压后
// 一个「大于打标延迟」的窗口，每行就会被重复下发几次直到包名到位，重发量
// 也被这个窗口限死。
export const PACKAGE_CURSOR_LAG_SECONDS = 5;

// Newest row that is already older than the lag window — the rows after it stay
// in front of the cursor and will be fetched again next tick.
//
// 取「已经老过滞后窗口」的最新一行——排在它之后的行留在游标前面，下一拍会被
// 再次取回。
function laggedCursor(items: TrafficRow[], lagSeconds: number): string | null {
  const newest = items[items.length - 1];
  if (!newest) return null;
  if (lagSeconds <= 0) return newest.id;
  // The newest drained row is the clock, not Date.now(): timestamps come from
  // the capture process, and the browser's clock may sit anywhere relative to it.
  //
  // 以最新一行的时间戳为基准而不是 Date.now()：时间戳由抓包进程写入，
  // 浏览器的时钟和它没有任何保证。
  const cutoff = newest.timestamp - lagSeconds;
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (item && item.timestamp <= cutoff) return item.id;
  }
  return null;
}

export function mergeTrafficPage(
  existing: TrafficRow[],
  page: TrafficRow[],
): TrafficRow[] {
  const byId = new Map<string, TrafficRow>();
  for (const row of existing) byId.set(row.id, row);
  for (const row of page) byId.set(row.id, row);
  return [...byId.values()].sort(
    (a, b) => a.timestamp - b.timestamp || a.id.localeCompare(b.id),
  );
}

export async function drainTraffic(options: {
  afterId: string | null;
  lagSeconds?: number;
  fetchPage: (args: {
    after_id?: string;
    limit: number;
    offset: number;
  }) => Promise<TrafficListPage>;
}): Promise<{ items: TrafficRow[]; newestId: string | null }> {
  let items: TrafficRow[] = [];
  const firstPageOnly = options.afterId === null;
  let offset = 0;

  while (true) {
    const page = await options.fetchPage({
      ...(options.afterId ? { after_id: options.afterId } : {}),
      limit: PAGE_SIZE,
      offset,
    });
    const rows = page.success ? page.requests ?? [] : [];
    items = mergeTrafficPage(items, rows);
    if (firstPageOnly) break;
    const returned = page.returned ?? rows.length;
    if (returned < PAGE_SIZE) break;
    offset += PAGE_SIZE;
  }

  return {
    items,
    // Fall back to the oldest row in this batch when there is no lagged
    // cursor AND no prior cursor to fall back to — otherwise the cursor
    // stays null forever whenever every row in a batch is inside the lag
    // window (easy once polling outpaces ~2 req/s), which permanently
    // caps delivery at PAGE_SIZE rows per tick and starts dropping rows.
    // `items` is sorted ascending (mergeTrafficPage), so items[0] is the
    // oldest row here; it was already delivered, so parking the cursor on
    // it just means the next tick re-fetches everything after it.
    //
    // 既没有滞后游标也没有旧游标可回退时，退化到本批最老的一行——否则
    // 只要一批里所有行都落在滞后窗口内（轮询速率超过约 2 请求/秒就会发生），
    // 游标就永远停在 null，把每拍下发量锁死在 PAGE_SIZE，进而开始丢行。
    // `items` 由 mergeTrafficPage 按时间升序排列，items[0] 就是本批最老的
    // 一行；它已经下发过了，游标停在它上面只会让下一拍重新拉取它之后的行。
    newestId:
      laggedCursor(items, options.lagSeconds ?? 0) ??
      options.afterId ??
      items[0]?.id ??
      null,
  };
}

/**
 * 列表已被清空（Clear / 重启后 ID 从 req-1 重来），但增量 cursor
 * 仍指着库里同名的新记录时，after_id 会把新握手全部滤掉。
 * 这时应按第一页重建分组，而不是继续等「比 cursor 更新」的帧。
 */
export function shouldRebuildFromFirstPage(options: {
  afterId: string | null;
  incrementalItems: number;
  visibleRows: number;
}): boolean {
  return (
    options.afterId != null &&
    options.incrementalItems === 0 &&
    options.visibleRows === 0
  );
}
