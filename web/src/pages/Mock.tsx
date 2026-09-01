import { json } from '@codemirror/lang-json';
import { oneDark } from '@codemirror/theme-one-dark';
import CodeMirror from '@uiw/react-codemirror';
import { useEffect, useRef, useState } from 'react';
import { callTool } from '../api';
import { JsonPane } from '../components/JsonPane';
import {
  bodyForEditor,
  bodyIsInvalidJson,
  draftToAddArgs,
  draftToUpdateArgs,
  emptyDraft,
  EXPORT_FILENAME,
  exportFileContents,
  findRuleById,
  formatBody,
  hasTruncatedBodies,
  isSaveBlockedByTruncation,
  mergeExportBodies,
  type ExportRule,
  type MockDraft,
  type MockRule,
  patchRuleEnabled,
  ruleToDraft,
} from './mockState';

type ToolEnvelope = {
  success?: boolean;
  message?: string;
  detail?: unknown;
};

type ListResult = ToolEnvelope & {
  rules?: MockRule[];
  total?: number;
  enabled_count?: number;
};

type ExportResult = ToolEnvelope & { rules?: ExportRule[] };

type MutateResult = ToolEnvelope & { rule?: MockRule };

type ToggleResult = ToolEnvelope & { rule_id?: string; enabled?: boolean };

type MockProps = {
  focusMockId?: string | null;
  onFocusConsumed?: () => void;
};

function toolError(result: ToolEnvelope, fallback: string): string {
  if (typeof result.message === 'string' && result.message) return result.message;
  if (typeof result.detail === 'string' && result.detail) return result.detail;
  return fallback;
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

function downloadJson(filename: string, text: string) {
  const blob = new Blob([text], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function Mock({ focusMockId, onFocusConsumed }: MockProps) {
  const [rules, setRules] = useState<MockRule[]>([]);
  const [total, setTotal] = useState(0);
  const [enabledCount, setEnabledCount] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [bodyLoadError, setBodyLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<MockDraft | null>(null);
  const [merge, setMerge] = useState(false);
  const [saving, setSaving] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  async function refresh(): Promise<MockRule[]> {
    const list = await callTool<ListResult>('mock_list');
    if (typeof list.success !== 'boolean') {
      throw new Error(toolError(list, 'control unavailable'));
    }
    if (!list.success) {
      setBanner(toolError(list, 'mock_list failed'));
      setLoaded(true);
      return [];
    }
    let next = list.rules ?? [];
    let loadError: string | null = null;
    if (hasTruncatedBodies(next)) {
      try {
        const exported = await callTool<ExportResult>('mock_export');
        if (typeof exported.success !== 'boolean') {
          throw new Error(toolError(exported, 'control unavailable'));
        }
        if (!exported.success || !exported.rules) {
          loadError = toolError(exported, '完整响应体未加载（mock_export 失败）');
        } else {
          next = mergeExportBodies(next, exported.rules);
          if (hasTruncatedBodies(next)) {
            loadError = '完整响应体未加载（mock_export 合并失败）';
          }
        }
      } catch (err) {
        loadError = err instanceof Error ? err.message : '完整响应体未加载（mock_export 失败）';
      }
    }
    setRules(next);
    setTotal(list.total ?? next.length);
    setEnabledCount(list.enabled_count ?? next.filter((rule) => rule.enabled).length);
    setBodyLoadError(loadError);
    if (loadError) {
      setBanner(loadError);
    } else {
      setBanner(null);
    }
    setLoaded(true);
    return next;
  }

  useEffect(() => {
    let cancelled = false;
    void refresh().catch((err) => {
      if (!cancelled) {
        setBanner(err instanceof Error ? err.message : 'mock_list failed');
        setLoaded(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!focusMockId || !loaded) return;
    const rule = findRuleById(rules, focusMockId);
    if (rule) {
      setDraft(ruleToDraft(rule));
      if (bodyForEditor(rule).truncated) {
        setBanner((prev) => prev ?? '完整响应体未加载，无法保存。');
      }
    } else {
      setDraft(null);
      setBanner(`规则不存在: ${focusMockId}`);
    }
    onFocusConsumed?.();
  }, [focusMockId, loaded, rules, onFocusConsumed]);

  function selectRule(rule: MockRule) {
    setDraft(ruleToDraft(rule));
  }

  function startNew() {
    setDraft(emptyDraft());
    setBanner(null);
  }

  function patchDraft<K extends keyof MockDraft>(key: K, value: MockDraft[K]) {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  async function toggleRule(rule: MockRule, enabled: boolean) {
    try {
      const result = await callTool<ToggleResult>('mock_toggle', {
        rule_id: rule.id,
        enabled,
      });
      if (typeof result.success !== 'boolean') {
        throw new Error(toolError(result, 'control unavailable'));
      }
      if (!result.success) {
        setBanner(toolError(result, 'toggle failed'));
        return;
      }
      const nextEnabled = result.enabled ?? enabled;
      setRules((prev) => patchRuleEnabled(prev, rule.id, nextEnabled));
      setEnabledCount((count) => count + (nextEnabled ? 1 : -1) * (rule.enabled === nextEnabled ? 0 : 1));
      setDraft((prev) => (prev?.id === rule.id ? { ...prev, enabled: nextEnabled } : prev));
      if (!bodyLoadError) setBanner(null);
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'toggle failed');
    }
  }

  async function saveDraft() {
    if (!draft) return;
    if (isSaveBlockedByTruncation(draft)) {
      setBanner('完整响应体未加载，无法保存。');
      return;
    }
    if (!draft.name.trim() || !draft.url_pattern.trim()) {
      setBanner('name 与 url_pattern 必填');
      return;
    }
    setSaving(true);
    try {
      let savedId = draft.id;
      if (draft.id) {
        const update = await callTool<MutateResult>('mock_update', draftToUpdateArgs(draft));
        if (typeof update.success !== 'boolean') {
          throw new Error(toolError(update, 'control unavailable'));
        }
        if (!update.success) {
          setBanner(toolError(update, 'update failed'));
          return;
        }
        const current = findRuleById(rules, draft.id);
        if (current && current.enabled !== draft.enabled) {
          const toggled = await callTool<ToggleResult>('mock_toggle', {
            rule_id: draft.id,
            enabled: draft.enabled,
          });
          if (toggled.success === false) {
            setBanner(toolError(toggled, 'toggle failed'));
          }
        }
      } else {
        const added = await callTool<MutateResult>('mock_add', draftToAddArgs(draft));
        if (typeof added.success !== 'boolean') {
          throw new Error(toolError(added, 'control unavailable'));
        }
        if (!added.success) {
          setBanner(toolError(added, 'add failed'));
          return;
        }
        savedId = added.rule?.id ?? null;
      }
      const next = await refresh();
      const selected = findRuleById(next, savedId);
      if (selected) setDraft(ruleToDraft(selected));
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'save failed');
    } finally {
      setSaving(false);
    }
  }

  async function deleteDraft() {
    if (!draft?.id) {
      setDraft(null);
      return;
    }
    if (!window.confirm(`删除规则 ${draft.name || draft.id}？`)) return;
    try {
      const result = await callTool<ToolEnvelope>('mock_delete', { rule_id: draft.id });
      if (typeof result.success !== 'boolean') {
        throw new Error(toolError(result, 'control unavailable'));
      }
      if (!result.success) {
        setBanner(toolError(result, 'delete failed'));
        return;
      }
      setDraft(null);
      await refresh();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'delete failed');
    }
  }

  async function clearAll() {
    if (!window.confirm('清空全部 Mock 规则？')) return;
    try {
      const result = await callTool<ToolEnvelope>('mock_clear');
      if (typeof result.success !== 'boolean') {
        throw new Error(toolError(result, 'control unavailable'));
      }
      if (!result.success) {
        setBanner(toolError(result, 'clear failed'));
        return;
      }
      setDraft(null);
      await refresh();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'clear failed');
    }
  }

  async function exportRules() {
    try {
      const result = await callTool<ExportResult>('mock_export');
      if (typeof result.success !== 'boolean') {
        throw new Error(toolError(result, 'control unavailable'));
      }
      if (!result.success) {
        setBanner(toolError(result, 'export failed'));
        return;
      }
      downloadJson(EXPORT_FILENAME, exportFileContents(result.rules ?? []));
      setBanner(null);
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'export failed');
    }
  }

  async function importFile(file: File) {
    try {
      const rulesJson = await file.text();
      const result = await callTool<ToolEnvelope>('mock_import', {
        rules_json: rulesJson,
        merge,
      });
      if (typeof result.success !== 'boolean') {
        throw new Error(toolError(result, 'control unavailable'));
      }
      if (!result.success) {
        setBanner(toolError(result, 'import failed'));
        return;
      }
      setDraft(null);
      await refresh();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : 'import failed');
    }
  }

  const invalidBody = draft ? bodyIsInvalidJson(draft.response_body) : false;
  const selectedId = draft?.id ?? null;

  return (
    <div className="mock">
      <div className="page-toolbar">
        <button type="button" className="primary" onClick={startNew}>
          New
        </button>
        <button type="button" onClick={() => void clearAll()}>
          Clear
        </button>
        <button type="button" onClick={() => void exportRules()}>
          Export
        </button>
        <label className="toolbar-check">
          <input
            type="checkbox"
            checked={merge}
            onChange={(event) => setMerge(event.target.checked)}
          />
          merge
        </label>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          aria-label="Import mock rules"
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void importFile(file);
          }}
        />
        <span className="muted toolbar-count">
          {enabledCount}/{total} 启用
        </span>
      </div>
      {banner ? (
        <p className={`page-banner ${bodyLoadError ? 'warn-banner' : 'muted'}`}>{banner}</p>
      ) : null}
      <div className="traffic-split">
        <div className="traffic-list">
          {rules.length === 0 ? (
            <div className="empty-state">
              <p className="muted">暂无 Mock 规则。</p>
              <button type="button" className="primary" onClick={startNew}>
                新建 Mock
              </button>
            </div>
          ) : (
            <table className="traffic-table mock-table">
              <thead>
                <tr>
                  <th>On</th>
                  <th>Name</th>
                  <th>Method</th>
                  <th>Pattern</th>
                  <th>Status</th>
                  <th>Hits</th>
                  <th>Delay</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((rule) => {
                  const selected = rule.id === selectedId;
                  const method = rule.method || '*';
                  const tone = statusTone(rule.status_code);
                  return (
                    <tr
                      key={rule.id}
                      className={selected ? 'selected' : undefined}
                      onClick={() => selectRule(rule)}
                    >
                      <td onClick={(event) => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={rule.enabled}
                          aria-label={`Toggle ${rule.name}`}
                          onChange={(event) => void toggleRule(rule, event.target.checked)}
                        />
                      </td>
                      <td className="url-cell" title={rule.name}>
                        <div className="url-cell-inner">
                          <span className="url-text">{rule.name}</span>
                        </div>
                      </td>
                      <td>
                        <span className="method">
                          <span
                            className="method-dot"
                            style={{ background: methodColor(method) }}
                          />
                          <span style={{ color: methodColor(method) }}>{method}</span>
                        </span>
                      </td>
                      <td className="url-cell" title={rule.url_pattern}>
                        <div className="url-cell-inner">
                          <span className="url-text">{rule.url_pattern}</span>
                        </div>
                      </td>
                      <td className={tone ? `status-${tone}` : 'muted'}>{rule.status_code}</td>
                      <td>{rule.hit_count}</td>
                      <td>{rule.delay_ms ? `${rule.delay_ms} ms` : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <aside className="inspector" aria-label="Mock inspector">
          {!draft ? (
            <p className="muted empty-state">Select a rule</p>
          ) : (
            <form
              className="inspector-form"
              onSubmit={(event) => {
                event.preventDefault();
                void saveDraft();
              }}
            >
              {draft.bodyTruncated ? (
                <p className="page-banner warn-banner">完整响应体未加载，无法保存。</p>
              ) : null}
              {invalidBody && !draft.bodyTruncated ? (
                <p className="page-banner warn-banner">非法 JSON，仍可保存为字符串。</p>
              ) : null}
              <label className="field">
                <span>Name</span>
                <input
                  value={draft.name}
                  onChange={(event) => patchDraft('name', event.target.value)}
                />
              </label>
              <label className="field">
                <span>URL pattern</span>
                <input
                  value={draft.url_pattern}
                  onChange={(event) => patchDraft('url_pattern', event.target.value)}
                />
              </label>
              <div className="field-row">
                <label className="field">
                  <span>Method</span>
                  <input
                    value={draft.method}
                    placeholder="全部"
                    onChange={(event) => patchDraft('method', event.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Match</span>
                  <select
                    value={draft.match_type}
                    onChange={(event) =>
                      patchDraft('match_type', event.target.value as MockDraft['match_type'])
                    }
                  >
                    <option value="contains">contains</option>
                    <option value="exact">exact</option>
                    <option value="regex">regex</option>
                  </select>
                </label>
              </div>
              <div className="field-row">
                <label className="field">
                  <span>Status</span>
                  <input
                    type="number"
                    value={draft.status_code}
                    onChange={(event) =>
                      patchDraft('status_code', Number(event.target.value) || 0)
                    }
                  />
                </label>
                <label className="field">
                  <span>Delay ms</span>
                  <input
                    type="number"
                    value={draft.delay_ms}
                    onChange={(event) =>
                      patchDraft('delay_ms', Number(event.target.value) || 0)
                    }
                  />
                </label>
              </div>
              <label className="field-check">
                <input
                  type="checkbox"
                  checked={draft.enabled}
                  onChange={(event) => patchDraft('enabled', event.target.checked)}
                />
                enabled
              </label>
              <div className="field">
                <span>Response body</span>
                <div className="json-pane-toolbar">
                  <button
                    type="button"
                    onClick={() => patchDraft('response_body', formatBody(draft.response_body).text)}
                  >
                    Format
                  </button>
                </div>
                <CodeMirror
                  value={draft.response_body}
                  theme={oneDark}
                  extensions={[json()]}
                  onChange={(value) => patchDraft('response_body', value)}
                  basicSetup={{
                    highlightActiveLine: false,
                    highlightActiveLineGutter: false,
                  }}
                />
              </div>
              <JsonPane raw={draft.response_body} />
              <div className="form-actions">
                <button
                  type="submit"
                  className="primary"
                  disabled={saving || isSaveBlockedByTruncation(draft)}
                >
                  Save
                </button>
                <button type="button" onClick={() => void deleteDraft()}>
                  Delete
                </button>
              </div>
            </form>
          )}
        </aside>
      </div>
    </div>
  );
}
