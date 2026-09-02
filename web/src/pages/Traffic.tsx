import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { callTool } from '../api';
import { HtmlBodyPane } from '../components/HtmlBodyPane';
import { ImageBodyPane } from '../components/ImageBodyPane';
import { JsonPane } from '../components/JsonPane';
import { BODY_LIMIT, bodyChunkCodePointLength, looksTruncated } from '../format';
import {
  drainTraffic,
  mergeTrafficPage,
  shouldRebuildFromFirstPage,
  type TrafficListPage,
  type TrafficRow,
} from '../poll/drainTraffic';
import {
  applyDetailListFields,
  COPY_MENU_ITEMS,
  countTrafficByKind,
  filterTrafficByKind,
  groupTrafficByPrefix,
  isSelectedInRows,
  looksLikeHtml,
  looksLikeImage,
  parseQueryPairs,
  patchRowFromDetail,
  resolveCopyText,
  TRAFFIC_KINDS,
  withoutConnectTunnels,
  type CopyKind,
  type CopyPayload,
  type TrafficKind,
} from './trafficState';
import {
  activeFilterCount,
  addPattern,
  filterTrafficByDisplayRules,
  hostGlobFromUrl,
  loadTrafficFilter,
  removePattern,
  saveTrafficFilter,
  type TrafficDisplayFilter,
} from './trafficFilter';

const POLL_FAST_MS = 1000;
const POLL_SLOW_MS = 3000;
const BODY_CHUNK = 4000;

type InspectorTab = 'Summary' | 'Request' | 'Response';
type PayloadTab = 'Headers' | 'Body';

type DetailRequest = {
  id: string;
  timestamp: number;
  method: string;
  url: string;
  domain: string;
  status: number;
  resource_type: string;
  response_size: number;
  time_ms: number;
  request_headers: Record<string, string>;
  request_body_size: number;
  response_headers: Record<string, string>;
  response_body_size: number;
  timing: Record<string, number>;
  error: string | null;
};

type ToolEnvelope = {
  success?: boolean;
  message?: string;
  detail?: unknown;
};

type DetailResult = ToolEnvelope & { request?: DetailRequest };

type BodyChunk = ToolEnvelope & {
  content?: string;
  has_more?: boolean;
  length?: number;
};

type SearchMatch = {
  request_id: string;
  url: string;
  method: string;
  domain: string;
  response_size: number;
};

type SearchResult = ToolEnvelope & { matches?: SearchMatch[] };

function pathOf(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.pathname + parsed.search;
  } catch {
    return url;
  }
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '—';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function methodColor(method: string): string {
  const m = method.toUpperCase();
  if (m === 'GET' || m === 'HEAD' || m === 'OPTIONS') return 'var(--get)';
  if (m === 'POST' || m === 'PUT' || m === 'PATCH') return 'var(--post)';
  if (m === 'DELETE') return 'var(--del)';
  return 'var(--muted)';
}

function statusTone(status: number): string {
  if (status >= 200 && status < 300) return 'ok';
  if (status >= 400 && status < 500) return 'warn';
  if (status >= 500) return 'err';
  return '';
}

function headerValue(
  headers: Record<string, string> | undefined,
  name: string,
): string | undefined {
  if (!headers) return undefined;
  const want = name.toLowerCase();
  for (const [key, value] of Object.entries(headers)) {
    if (key.toLowerCase() === want) return value;
  }
  return undefined;
}

function mockRuleId(headers: Record<string, string> | undefined): string | undefined {
  return headerValue(headers, 'X-Mock-Rule') ?? headerValue(headers, 'x-mock-rule');
}

function toolError(result: ToolEnvelope, fallback: string): string {
  if (typeof result.message === 'string' && result.message) return result.message;
  if (typeof result.detail === 'string' && result.detail) return result.detail;
  return fallback;
}

function assertToolPage(page: TrafficListPage & ToolEnvelope): TrafficListPage {
  if (typeof page.success !== 'boolean') {
    throw new Error(toolError(page, 'control unavailable'));
  }
  return page;
}

function matchToRow(match: SearchMatch): TrafficRow {
  return {
    id: match.request_id,
    timestamp: 0,
    method: match.method,
    url: match.url,
    domain: match.domain,
    status: 0,
    type: '',
    size: match.response_size,
    time: 0,
    error: null,
  };
}

async function readBodyFull(
  requestId: string,
  field: 'request_body' | 'response_body',
): Promise<{ text: string; truncated: boolean }> {
  let offset = 0;
  let text = '';
  let hasMore = false;

  while (text.length < BODY_LIMIT) {
    const length = Math.min(BODY_CHUNK, BODY_LIMIT - text.length);
    const chunk = await callTool<BodyChunk>('traffic_read_body', {
      request_id: requestId,
      field,
      offset,
      length,
    });
    if (!chunk.success) {
      return { text, truncated: looksTruncated(false, text.length) };
    }
    const content = chunk.content ?? '';
    text += content;
    hasMore = Boolean(chunk.has_more);
    // 必须用服务端码点长度推进 offset；JS string.length 会把 emoji 算成 2，导致分片错位。
    const step = bodyChunkCodePointLength(chunk);
    if (!hasMore) break;
    if (step === 0) break;
    offset += step;
  }

  if (text.length > BODY_LIMIT) text = text.slice(0, BODY_LIMIT);
  return { text, truncated: looksTruncated(hasMore, text.length) };
}

type TrafficProps = {
  onOpenMock?: (ruleId: string) => void;
};

export default function Traffic({ onOpenMock }: TrafficProps) {
  const [rows, setRows] = useState<TrafficRow[]>([]);
  const [urlDraft, setUrlDraft] = useState('');
  const [filterUrl, setFilterUrl] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [mode, setMode] = useState<'live' | 'search'>('live');
  const [banner, setBanner] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedRow, setSelectedRow] = useState<TrafficRow | null>(null);
  const [detail, setDetail] = useState<DetailRequest | null>(null);
  const [reqBody, setReqBody] = useState('');
  const [resBody, setResBody] = useState('');
  const [truncated, setTruncated] = useState({ req: false, res: false });
  const [tab, setTab] = useState<InspectorTab>('Response');
  const [payloadTab, setPayloadTab] = useState<PayloadTab>('Body');
  const [mockById, setMockById] = useState<Record<string, string>>({});
  const [detailMissing, setDetailMissing] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [copyMenu, setCopyMenu] = useState<{
    rowId: string;
    top: number;
    left: number;
  } | null>(null);
  const [kind, setKind] = useState<TrafficKind>('all');
  const [displayFilter, setDisplayFilter] = useState<TrafficDisplayFilter>(() => loadTrafficFilter());
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterTab, setFilterTab] = useState<'allow' | 'ignore'>('allow');
  const [patternDraft, setPatternDraft] = useState('');

  const afterIdRef = useRef<string | null>(null);
  const generationRef = useRef(0);
  const selectedIdRef = useRef<string | null>(null);
  const rowsCountRef = useRef(0);
  selectedIdRef.current = selectedId;
  rowsCountRef.current = rows.length;

  function clearInspector() {
    generationRef.current += 1;
    setSelectedId(null);
    setSelectedRow(null);
    setDetail(null);
    setReqBody('');
    setResBody('');
    setTruncated({ req: false, res: false });
    setDetailMissing(false);
  }

  const copyKind = useCallback(async (row: TrafficRow, kind: CopyKind) => {
    let payload: CopyPayload = { method: row.method, url: row.url };
    if (kind !== 'api' && kind !== 'url') {
      try {
        const [detailRes, requestBody, responseBody] = await Promise.all([
          callTool<DetailResult>('traffic_get_detail', { request_id: row.id }),
          readBodyFull(row.id, 'request_body'),
          readBodyFull(row.id, 'response_body'),
        ]);
        payload = {
          method: detailRes.request?.method || row.method,
          url: detailRes.request?.url || row.url,
          requestHeaders: detailRes.request?.request_headers,
          requestBody: requestBody.text,
          responseHeaders: detailRes.request?.response_headers,
          responseBody: responseBody.text,
        };
      } catch {
        // still copy URL / method
      }
    }
    const text = resolveCopyText(kind, payload);
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(row.id);
      window.setTimeout(() => {
        setCopiedId((current) => (current === row.id ? null : current));
      }, 1500);
    } catch {
      setBanner('无法写入剪贴板');
    }
  }, []);

  function openCopyMenu(event: MouseEvent<HTMLButtonElement>, rowId: string) {
    event.stopPropagation();
    if (copyMenu?.rowId === rowId) {
      setCopyMenu(null);
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const width = 148;
    const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
    const top = Math.min(rect.bottom + 4, window.innerHeight - 260);
    setCopyMenu({ rowId, top, left });
  }

  useEffect(() => {
    if (!copyMenu) return;
    function onDoc(event: Event) {
      const target = event.target as HTMLElement | null;
      if (target?.closest('.copy-menu') || target?.closest('.curl-btn')) return;
      setCopyMenu(null);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setCopyMenu(null);
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [copyMenu]);

  function pruneIfMissing(nextRows: TrafficRow[]) {
    const id = selectedIdRef.current;
    if (id != null && !isSelectedInRows(nextRows, id)) {
      clearInspector();
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => setFilterUrl(urlDraft.trim()), 400);
    return () => window.clearTimeout(timer);
  }, [urlDraft]);

  useEffect(() => {
    if (mode !== 'live') return;

    afterIdRef.current = null;

    let cancelled = false;
    let timer = 0;
    let inFlight = false;
    let delay = POLL_FAST_MS;
    let replaceOnSuccess = true;

    async function tick() {
      if (cancelled) return;
      if (document.visibilityState !== 'visible') return;
      if (inFlight) return;
      inFlight = true;
      try {
        let listOk = false;
        const cursor = afterIdRef.current;
        const fetchPage = async (args: {
          after_id?: string;
          limit: number;
          offset: number;
        }) => {
          const page = await callTool<TrafficListPage & ToolEnvelope>('traffic_list', {
            ...args,
            ...(filterUrl ? { filter_url: filterUrl } : {}),
          });
          const checked = assertToolPage(page);
          listOk = checked.success;
          if (!checked.success) {
            setBanner(toolError(checked, 'traffic_list failed'));
          } else {
            setBanner(null);
          }
          return checked;
        };
        const result = await drainTraffic({
          afterId: cursor,
          fetchPage,
        });
        if (cancelled) return;
        const items = withoutConnectTunnels(result.items);
        if (
          listOk &&
          shouldRebuildFromFirstPage({
            afterId: cursor,
            incrementalItems: items.length,
            visibleRows: rowsCountRef.current,
          })
        ) {
          const full = await drainTraffic({
            afterId: null,
            fetchPage,
          });
          if (cancelled) return;
          const rebuilt = withoutConnectTunnels(full.items);
          if (rebuilt.length > 0) {
            replaceOnSuccess = false;
            setRows(rebuilt);
            setCollapsed({});
            pruneIfMissing(rebuilt);
            if (full.newestId) afterIdRef.current = full.newestId;
          } else {
            afterIdRef.current = null;
          }
          delay = POLL_FAST_MS;
          return;
        }
        if (replaceOnSuccess) {
          if (listOk) {
            replaceOnSuccess = false;
            setRows(items);
            pruneIfMissing(items);
          }
        } else {
          setRows((prev) => mergeTrafficPage(prev, items));
        }
        if (items.length > 0 && result.newestId) {
          afterIdRef.current = result.newestId;
        }
        delay = POLL_FAST_MS;
      } catch (err) {
        if (!cancelled) {
          setBanner(err instanceof Error ? err.message : 'control unavailable');
          delay = POLL_SLOW_MS;
        }
      } finally {
        inFlight = false;
        if (!cancelled && document.visibilityState === 'visible') {
          timer = window.setTimeout(() => void tick(), delay);
        }
      }
    }

    function onVisibility() {
      window.clearTimeout(timer);
      if (document.visibilityState === 'visible') void tick();
    }

    document.addEventListener('visibilitychange', onVisibility);
    void tick();

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [mode, filterUrl]);

  const selectRow = useCallback((row: TrafficRow) => {
    const generation = ++generationRef.current;
    setSelectedId(row.id);
    setSelectedRow(row);
    setDetail(null);
    setReqBody('');
    setResBody('');
    setTruncated({ req: false, res: false });
    setDetailMissing(false);
    setTab('Response');
    setPayloadTab('Body');

    void (async () => {
      try {
        const [detailRes, requestBody, responseBody] = await Promise.all([
          callTool<DetailResult>('traffic_get_detail', { request_id: row.id }),
          readBodyFull(row.id, 'request_body'),
          readBodyFull(row.id, 'response_body'),
        ]);
        if (generation !== generationRef.current) return;

        if (detailRes.success && detailRes.request) {
          const req = detailRes.request;
          setDetail(req);
          setSelectedRow((prev) =>
            prev?.id === req.id ? applyDetailListFields(prev, req) : prev,
          );
          setRows((prev) => patchRowFromDetail(prev, req));
          const rule = mockRuleId(req.response_headers);
          if (rule) {
            setMockById((prev) => ({ ...prev, [row.id]: rule }));
          }
        } else {
          setDetailMissing(true);
          setBanner(toolError(detailRes, 'This record no longer exists.'));
        }
        setReqBody(requestBody.text);
        setResBody(responseBody.text);
        setTruncated({ req: requestBody.truncated, res: responseBody.truncated });
      } catch (err) {
        if (generation !== generationRef.current) return;
        setBanner(err instanceof Error ? err.message : 'detail failed');
      }
    })();
  }, []);

  async function submitSearch() {
    const keyword = searchDraft.trim();
    if (!keyword) {
      setMode('live');
      setBanner(null);
      return;
    }
    try {
      const result = await callTool<SearchResult>('traffic_search', {
        keyword,
        search_in: ['all'],
      });
      if (typeof result.success !== 'boolean') {
        throw new Error(toolError(result, 'control unavailable'));
      }
      if (!result.success) {
        setBanner(toolError(result, 'search failed'));
        return;
      }
      const next = withoutConnectTunnels((result.matches ?? []).map(matchToRow));
      setMode('search');
      setRows(next);
      setBanner(null);
      pruneIfMissing(next);
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'search failed');
    }
  }

  async function clearTraffic() {
    if (!window.confirm('清空全部流量记录？')) return;
    try {
      const result = await callTool<ToolEnvelope>('traffic_clear');
      if (result.success === false) {
        setBanner(toolError(result, 'clear failed'));
        return;
      }
      if (typeof result.success !== 'boolean') {
        throw new Error(toolError(result, 'control unavailable'));
      }
      afterIdRef.current = null;
      generationRef.current += 1;
      setRows([]);
      setCollapsed({});
      setKind('all');
      setSelectedId(null);
      setSelectedRow(null);
      setDetail(null);
      setReqBody('');
      setResBody('');
      setMockById({});
      setBanner(null);
      setMode('live');
      setSearchDraft('');
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'clear failed');
    }
  }

  const summary = detail
    ? {
        url: detail.url,
        method: detail.method,
        status: detail.status,
        type: detail.resource_type,
        size: detail.response_size,
        time: detail.time_ms,
        error: detail.error,
      }
    : selectedRow
      ? {
          url: selectedRow.url,
          method: selectedRow.method,
          status: selectedRow.status,
          type: selectedRow.type,
          size: selectedRow.size,
          time: selectedRow.time,
          error: selectedRow.error,
        }
      : null;

  function patchDisplayFilter(next: TrafficDisplayFilter) {
    setDisplayFilter(next);
    saveTrafficFilter(next);
  }

  function addCurrentPattern() {
    const pattern = patternDraft.trim();
    if (!pattern) return;
    if (filterTab === 'allow') {
      patchDisplayFilter({
        ...displayFilter,
        allow: addPattern(displayFilter.allow, pattern),
      });
    } else {
      patchDisplayFilter({
        ...displayFilter,
        ignore: addPattern(displayFilter.ignore, pattern),
      });
    }
    setPatternDraft('');
  }

  function quickAllowHost(prefix: string) {
    const pattern = hostGlobFromUrl(prefix);
    patchDisplayFilter({
      ...displayFilter,
      enabled: true,
      allow: addPattern(displayFilter.allow, pattern),
    });
    setFilterTab('allow');
  }

  function quickIgnoreHost(prefix: string) {
    const pattern = hostGlobFromUrl(prefix);
    patchDisplayFilter({
      ...displayFilter,
      enabled: true,
      ignore: addPattern(displayFilter.ignore, pattern),
    });
    setFilterTab('ignore');
  }

  const displayRows = useMemo(
    () => filterTrafficByDisplayRules(rows, displayFilter),
    [rows, displayFilter],
  );
  const visibleRows = useMemo(
    () => filterTrafficByKind(displayRows, kind),
    [displayRows, kind],
  );
  const kindCounts = useMemo(() => countTrafficByKind(displayRows), [displayRows]);
  const groups = useMemo(() => groupTrafficByPrefix(visibleRows), [visibleRows]);
  const filterRuleCount = activeFilterCount(displayFilter);
  const copyMenuRow =
    copyMenu &&
    (rows.find((item) => item.id === copyMenu.rowId) ??
      (selectedRow?.id === copyMenu.rowId ? selectedRow : null));
  const responseHtml =
    Boolean(resBody) &&
    looksLikeHtml(
      resBody,
      headerValue(detail?.response_headers, 'content-type'),
      summary?.type ?? selectedRow?.type,
    );
  const responseImage = looksLikeImage(
    summary?.url ?? selectedRow?.url ?? '',
    headerValue(detail?.response_headers, 'content-type'),
    summary?.type ?? selectedRow?.type,
  );
  const summaryQueryPairs = useMemo(
    () => (summary?.url ? parseQueryPairs(summary.url) : []),
    [summary?.url],
  );

  return (
    <div className="traffic">
      <div className="page-toolbar">
        <input
          className="toolbar-input"
          type="search"
          placeholder="filter URL"
          value={urlDraft}
          disabled={mode === 'search'}
          onChange={(event) => setUrlDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') setFilterUrl(urlDraft.trim());
          }}
          aria-label="Filter URL"
        />
        <input
          className="toolbar-input"
          type="search"
          placeholder="search"
          value={searchDraft}
          onChange={(event) => setSearchDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submitSearch();
          }}
          aria-label="Search traffic"
        />
        <button type="button" onClick={() => void submitSearch()}>
          Search
        </button>
        <button type="button" onClick={() => void clearTraffic()}>
          Clear
        </button>
        <button
          type="button"
          className={filterOpen || filterRuleCount > 0 ? 'primary' : undefined}
          aria-expanded={filterOpen}
          aria-controls="traffic-display-filter"
          onClick={() => setFilterOpen((open) => !open)}
        >
          过滤{filterRuleCount > 0 ? ` ${filterRuleCount}` : ''}
        </button>
        <span className="muted toolbar-count">
          {visibleRows.length}
          {displayRows.length !== rows.length ? ` / ${rows.length}` : ''} 条
        </span>
      </div>
      {filterOpen ? (
        <div className="traffic-filter-panel" id="traffic-display-filter">
          <div className="traffic-filter-bar">
            <label className="traffic-filter-enable">
              <input
                type="checkbox"
                checked={displayFilter.enabled}
                onChange={(event) =>
                  patchDisplayFilter({ ...displayFilter, enabled: event.target.checked })
                }
              />
              启用列表过滤（仅隐藏展示，仍入库）
            </label>
            <span className="muted">
              类似 Charles Focus / Proxyman Hide：Allow 非空时只显示匹配项；Ignore 命中则隐藏
            </span>
          </div>
          <div className="payload-tabs" role="tablist" aria-label="Allow or Ignore">
            <button
              type="button"
              role="tab"
              aria-selected={filterTab === 'allow'}
              className={filterTab === 'allow' ? 'tab active' : 'tab'}
              onClick={() => setFilterTab('allow')}
            >
              只显示 ({displayFilter.allow.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={filterTab === 'ignore'}
              className={filterTab === 'ignore' ? 'tab active' : 'tab'}
              onClick={() => setFilterTab('ignore')}
            >
              忽略 ({displayFilter.ignore.length})
            </button>
          </div>
          <div className="traffic-filter-add">
            <input
              className="toolbar-input"
              type="text"
              placeholder={
                filterTab === 'allow'
                  ? '例如 *.flowgpt.com/*'
                  : '例如 *googleapis.com* 或 *.facebook.com/*'
              }
              value={patternDraft}
              onChange={(event) => setPatternDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') addCurrentPattern();
              }}
              aria-label={filterTab === 'allow' ? 'Allow pattern' : 'Ignore pattern'}
            />
            <button type="button" className="primary" onClick={addCurrentPattern}>
              添加
            </button>
            {filterTab === 'allow' ? (
              <button
                type="button"
                onClick={() => {
                  patchDisplayFilter({
                    ...displayFilter,
                    enabled: true,
                    allow: addPattern(displayFilter.allow, '*.flowgpt.com/*'),
                  });
                }}
              >
                + flowgpt
              </button>
            ) : null}
          </div>
          <ul className="traffic-filter-list">
            {(filterTab === 'allow' ? displayFilter.allow : displayFilter.ignore).length === 0 ? (
              <li className="muted">
                {filterTab === 'allow'
                  ? '暂无规则：Allow 为空时显示全部（再被 Ignore 裁剪）。'
                  : '暂无忽略规则。'}
              </li>
            ) : (
              (filterTab === 'allow' ? displayFilter.allow : displayFilter.ignore).map((pattern) => (
                <li key={pattern}>
                  <code className="mono">{pattern}</code>
                  <button
                    type="button"
                    className="curl-btn"
                    onClick={() => {
                      if (filterTab === 'allow') {
                        patchDisplayFilter({
                          ...displayFilter,
                          allow: removePattern(displayFilter.allow, pattern),
                        });
                      } else {
                        patchDisplayFilter({
                          ...displayFilter,
                          ignore: removePattern(displayFilter.ignore, pattern),
                        });
                      }
                    }}
                  >
                    删除
                  </button>
                </li>
              ))
            )}
          </ul>
        </div>
      ) : null}
      <div className="traffic-kinds" role="tablist" aria-label="Traffic kind">
        {TRAFFIC_KINDS.map((item) => {
          const count = kindCounts[item.id];
          const active = kind === item.id;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={active}
              className={active ? 'kind-tab active' : 'kind-tab'}
              onClick={() => setKind(item.id)}
            >
              {item.label}
              <span className="kind-count">{count}</span>
            </button>
          );
        })}
      </div>
      {banner ? <p className="page-banner muted">{banner}</p> : null}
      <div className="traffic-split">
        <div className="traffic-list">
          {rows.length === 0 ? (
            <p className="empty-state muted">
              {mode === 'search'
                ? '没有匹配的流量。'
                : '暂无流量。启动代理后发请求即可看到列表。'}
            </p>
          ) : visibleRows.length === 0 ? (
            <p className="empty-state muted">
              {displayRows.length === 0 && rows.length > 0
                ? '当前过滤规则下没有流量。可关闭「过滤」或调整 Allow / Ignore。'
                : '这一类暂无流量。'}
            </p>
          ) : (
            <table className="traffic-table">
              <thead>
                <tr>
                  <th>Method</th>
                  <th>Status</th>
                  <th>URL</th>
                  <th>Size</th>
                  <th>Time</th>
                  <th>cURL</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => {
                  const folded = Boolean(collapsed[group.prefix]);
                  return (
                    <Fragment key={group.prefix}>
                      <tr className="traffic-group">
                        <td colSpan={6}>
                          <div className="traffic-group-row">
                            <button
                              type="button"
                              className="traffic-group-toggle"
                              aria-expanded={!folded}
                              onClick={() =>
                                setCollapsed((prev) => ({
                                  ...prev,
                                  [group.prefix]: !prev[group.prefix],
                                }))
                              }
                            >
                              <span className="group-chevron" aria-hidden="true">
                                {folded ? '▸' : '▾'}
                              </span>
                              <span className="mono url-text">{group.prefix}</span>
                              <span className="muted">{group.rows.length}</span>
                            </button>
                            <span className="traffic-group-actions">
                              <button
                                type="button"
                                className="curl-btn"
                                title={`只显示 ${hostGlobFromUrl(group.prefix)}`}
                                onClick={() => quickAllowHost(group.prefix)}
                              >
                                只显示
                              </button>
                              <button
                                type="button"
                                className="curl-btn"
                                title={`忽略 ${hostGlobFromUrl(group.prefix)}`}
                                onClick={() => quickIgnoreHost(group.prefix)}
                              >
                                忽略
                              </button>
                            </span>
                          </div>
                        </td>
                      </tr>
                      {folded
                        ? null
                        : group.rows.map((row) => {
                            const selected = row.id === selectedId;
                            const mock = mockById[row.id];
                            return (
                              <tr
                                key={row.id}
                                className={selected ? 'selected' : undefined}
                                onClick={() => selectRow(row)}
                              >
                                <td>
                                  <span className="method">
                                    <span
                                      className="method-dot"
                                      style={{ background: methodColor(row.method) }}
                                    />
                                    <span style={{ color: methodColor(row.method) }}>
                                      {row.method}
                                    </span>
                                  </span>
                                </td>
                                <td
                                  className={
                                    statusTone(row.status)
                                      ? `status-${statusTone(row.status)}`
                                      : 'muted'
                                  }
                                >
                                  {row.status || '—'}
                                </td>
                                <td className="url-cell" title={row.url}>
                                  <div className="url-cell-inner">
                                    <span className="url-text">{pathOf(row.url)}</span>
                                    {mock ? (
                                      <span
                                        className="mock-badge"
                                        title={`Mock ${mock}`}
                                        role="button"
                                        onClick={(event) => {
                                          event.stopPropagation();
                                          onOpenMock?.(mock);
                                        }}
                                      >
                                        MOCK
                                      </span>
                                    ) : null}
                                  </div>
                                </td>
                                <td>{formatBytes(row.size)}</td>
                                <td>{row.time ? `${Math.round(row.time)} ms` : '—'}</td>
                                <td
                                  className="copy-cell"
                                  onClick={(event) => event.stopPropagation()}
                                >
                                  <button
                                    type="button"
                                    className="curl-btn"
                                    aria-haspopup="menu"
                                    aria-expanded={copyMenu?.rowId === row.id}
                                    onClick={(event) => openCopyMenu(event, row.id)}
                                  >
                                    {copiedId === row.id ? '已复制' : '复制'}
                                  </button>
                                </td>
                              </tr>
                            );
                          })}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <aside className="inspector" aria-label="Request inspector">
          {!selectedRow ? (
            <p className="muted empty-state">Select a request</p>
          ) : (
            <>
              {truncated.req || truncated.res ? (
                <p className="page-banner muted">
                  Body truncated at {BODY_LIMIT.toLocaleString()} bytes
                </p>
              ) : null}
              {detailMissing ? (
                <p className="page-banner muted">This record no longer exists.</p>
              ) : null}
              <div className="inspector-tabs" role="tablist">
                {(['Summary', 'Request', 'Response'] as const).map((name) => (
                  <button
                    key={name}
                    type="button"
                    role="tab"
                    aria-selected={tab === name}
                    className={tab === name ? 'tab active' : 'tab'}
                    onClick={() => setTab(name)}
                  >
                    {name}
                  </button>
                ))}
                <button
                  type="button"
                  className="curl-btn"
                  aria-haspopup="menu"
                  aria-expanded={copyMenu?.rowId === selectedRow.id}
                  onClick={(event) => openCopyMenu(event, selectedRow.id)}
                >
                  {copiedId === selectedRow.id ? '已复制' : '复制'}
                </button>
              </div>
              {tab === 'Summary' && summary ? (
                <dl className="summary-list">
                  <div>
                    <dt>URL</dt>
                    <dd className="mono">{summary.url}</dd>
                  </div>
                  {summaryQueryPairs.length > 0 ? (
                    <div>
                      <dt>Query</dt>
                      <dd>
                        <KvTable pairs={summaryQueryPairs} />
                      </dd>
                    </div>
                  ) : null}
                  <div>
                    <dt>Method</dt>
                    <dd>{summary.method}</dd>
                  </div>
                  <div>
                    <dt>Status</dt>
                    <dd>{summary.status || '—'}</dd>
                  </div>
                  <div>
                    <dt>Type</dt>
                    <dd>{summary.type || '—'}</dd>
                  </div>
                  <div>
                    <dt>Size</dt>
                    <dd>{formatBytes(summary.size)}</dd>
                  </div>
                  <div>
                    <dt>Time</dt>
                    <dd>{summary.time ? `${Math.round(summary.time)} ms` : '—'}</dd>
                  </div>
                  <div>
                    <dt>Error</dt>
                    <dd>{summary.error || '—'}</dd>
                  </div>
                </dl>
              ) : null}
              {tab === 'Request' ? (
                <div className="inspector-body">
                  <PayloadTabs value={payloadTab} onChange={setPayloadTab} />
                  {payloadTab === 'Headers' ? (
                    <>
                      <InspectorSection title="Query">
                        <QueryBlock url={summary?.url ?? selectedRow.url} />
                      </InspectorSection>
                      <InspectorSection title="Headers">
                        <HeaderBlock headers={detail?.request_headers} />
                      </InspectorSection>
                    </>
                  ) : reqBody ? (
                    <JsonPane raw={reqBody} />
                  ) : (
                    <RequestEmptyBody
                      method={(summary?.method ?? selectedRow.method) || 'GET'}
                      url={summary?.url ?? selectedRow.url}
                    />
                  )}
                </div>
              ) : null}
              {tab === 'Response' ? (
                <div className="inspector-body">
                  <PayloadTabs value={payloadTab} onChange={setPayloadTab} />
                  {payloadTab === 'Headers' ? (
                    <HeaderBlock headers={detail?.response_headers} />
                  ) : responseImage ? (
                    <ImageBodyPane url={summary?.url ?? selectedRow.url} />
                  ) : resBody ? (
                    responseHtml ? (
                      <HtmlBodyPane raw={resBody} />
                    ) : (
                      <JsonPane raw={resBody} />
                    )
                  ) : (
                    <p className="muted">empty</p>
                  )}
                </div>
              ) : null}
            </>
          )}
        </aside>
      </div>
      {copyMenu && copyMenuRow ? (
        <div
          className="copy-menu"
          role="menu"
          style={{ top: copyMenu.top, left: copyMenu.left }}
        >
          {COPY_MENU_ITEMS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              onClick={(event) => {
                event.stopPropagation();
                setCopyMenu(null);
                void copyKind(copyMenuRow, item.id);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PayloadTabs({
  value,
  onChange,
}: {
  value: PayloadTab;
  onChange: (tab: PayloadTab) => void;
}) {
  return (
    <div className="payload-tabs" role="tablist" aria-label="Headers or Body">
      {(['Headers', 'Body'] as const).map((name) => (
        <button
          key={name}
          type="button"
          role="tab"
          aria-selected={value === name}
          className={value === name ? 'tab active' : 'tab'}
          onClick={() => onChange(name)}
        >
          {name}
        </button>
      ))}
    </div>
  );
}

function InspectorSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="inspector-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function RequestEmptyBody({ method, url }: { method: string; url: string }) {
  const pairs = parseQueryPairs(url);
  if (pairs.length > 0) {
    return (
      <div className="query-body">
        <p className="muted query-body-hint">
          {method.toUpperCase() === 'GET'
            ? 'GET 没有 body，以下为 URL 查询参数（已解码）：'
            : '无 request body，以下为 URL 查询参数（已解码）：'}
        </p>
        <KvTable pairs={pairs} />
      </div>
    );
  }
  return (
    <p className="muted">
      {method.toUpperCase() === 'GET' ? 'GET 没有 body，也没有查询参数。' : 'empty'}
    </p>
  );
}

function QueryBlock({ url }: { url: string }) {
  const pairs = parseQueryPairs(url);
  if (pairs.length === 0) {
    return <p className="muted">No query</p>;
  }
  return <KvTable pairs={pairs} />;
}

function KvTable({ pairs }: { pairs: { name: string; value: string }[] }) {
  return (
    <table className="kv-table">
      <thead>
        <tr>
          <th scope="col">Key</th>
          <th scope="col">Value</th>
        </tr>
      </thead>
      <tbody>
        {pairs.map((pair, index) => (
          <tr key={`${pair.name}:${index}`}>
            <td className="kv-key mono">{pair.name}</td>
            <td className="kv-value mono">{pair.value || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PairList({
  pairs,
  empty,
}: {
  pairs: { name: string; value: string }[];
  empty: string;
}) {
  if (pairs.length === 0) {
    return <p className="muted">{empty}</p>;
  }
  return (
    <ul className="header-list">
      {pairs.map((pair, index) => (
        <li key={`${pair.name}:${index}`}>
          <span className="header-name">{pair.name}</span>
          <span className="header-value">{pair.value}</span>
        </li>
      ))}
    </ul>
  );
}

function HeaderBlock({ headers }: { headers?: Record<string, string> }) {
  if (!headers || Object.keys(headers).length === 0) {
    return <p className="muted">No headers</p>;
  }
  return (
    <PairList
      pairs={Object.entries(headers).map(([name, value]) => ({ name, value }))}
      empty="No headers"
    />
  );
}
