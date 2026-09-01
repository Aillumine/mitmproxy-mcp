import { useCallback, useEffect, useState } from 'react';
import { callTool } from '../api';
import { JsonPane } from '../components/JsonPane';
import {
  IOS_POLL_MS,
  IOS_TABS,
  emptyMessage,
  isSelectedPresent,
  listToolForTab,
  parseDevices,
  resultToJson,
  selectedDevice,
  showSimulatorActions,
  udidArgs,
  type IosDevice,
  type IosTab,
  type ToolEnvelope,
} from './iosState';

type ListResult = ToolEnvelope & { devices?: unknown[] };

export default function Ios() {
  const [tab, setTab] = useState<IosTab>('all');
  const [devices, setDevices] = useState<IosDevice[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [actionBanner, setActionBanner] = useState<string | null>(null);
  const [resultJson, setResultJson] = useState('');
  const [busy, setBusy] = useState(false);

  const listTool = listToolForTab(tab);

  const refreshDevices = useCallback(async () => {
    const result = await callTool<ListResult>(listTool);
    const next = parseDevices(result, tab);
    setDevices(next);
    setLoaded(true);
    setSelected((cur) => (isSelectedPresent(next, cur) ? cur : null));
    setListError(result.success === false ? (result.message ?? `${listTool} failed`) : null);
    return next;
  }, [listTool, tab]);

  useEffect(() => {
    setDevices([]);
    setLoaded(false);
    setSelected(null);
    setResultJson('');
    setListError(null);

    let cancelled = false;
    let timer = 0;
    let inFlight = false;

    async function tick() {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        await refreshDevices();
      } catch (err) {
        if (!cancelled) {
          setListError(err instanceof Error ? err.message : `${listTool} failed`);
          setLoaded(true);
        }
      } finally {
        inFlight = false;
        if (!cancelled) {
          timer = window.setTimeout(() => void tick(), IOS_POLL_MS);
        }
      }
    }

    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [refreshDevices, listTool]);

  async function selectDevice(udid: string) {
    setSelected(udid);
    try {
      const info = await callTool<ToolEnvelope>('ios_get_device_info', udidArgs(udid));
      setResultJson(resultToJson(info));
      if (info.success === false) {
        setActionBanner(info.message ?? 'ios_get_device_info failed');
      } else {
        setActionBanner(null);
      }
    } catch (err) {
      setActionBanner(err instanceof Error ? err.message : 'ios_get_device_info failed');
    }
  }

  async function runAction(name: string, udid: string) {
    setBusy(true);
    try {
      const result = await callTool<ToolEnvelope>(name, udidArgs(udid));
      setResultJson(resultToJson(result));
      if (result.success === false) {
        setActionBanner(result.message ?? `${name} failed`);
      } else {
        setActionBanner(null);
      }
      await refreshDevices();
    } catch (err) {
      setActionBanner(err instanceof Error ? err.message : `${name} failed`);
    } finally {
      setBusy(false);
    }
  }

  const current = selectedDevice(devices, selected);
  const simulatorActions = showSimulatorActions(current);

  return (
    <div className="ios">
      <div className="page-toolbar">
        <div className="ios-tabs" role="tablist" aria-label="iOS device type">
          {IOS_TABS.map((item) => {
            const active = tab === item.id;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={active}
                className={active ? 'tab active' : 'tab'}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </button>
            );
          })}
        </div>
        <span className="muted toolbar-count">{devices.length} 台</span>
      </div>
      {actionBanner || listError ? (
        <p className="page-banner warn-banner">{actionBanner ?? listError}</p>
      ) : null}
      <div className="traffic-split">
        <div className="traffic-list">
          {loaded && devices.length === 0 ? (
            <p className="empty-state muted">{emptyMessage(tab)}</p>
          ) : (
            <table className="traffic-table ios-table">
              <thead>
                <tr>
                  <th>udid</th>
                  <th>name</th>
                  <th>state</th>
                  <th>type</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => {
                  const active = device.udid === selected;
                  return (
                    <tr
                      key={device.udid}
                      className={active ? 'selected' : undefined}
                      onClick={() => void selectDevice(device.udid)}
                    >
                      <td className="mono">{device.udid}</td>
                      <td>{device.name || '—'}</td>
                      <td>{device.state || '—'}</td>
                      <td>{device.isSimulator ? '模拟器' : '真机'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <aside className="inspector" aria-label="iOS device">
          {!selected ? (
            <p className="muted empty-state">选择一台设备</p>
          ) : (
            <>
              {simulatorActions ? (
                <div className="ios-actions">
                  <button
                    className="primary"
                    type="button"
                    disabled={busy}
                    onClick={() => void runAction('ios_boot_simulator', selected)}
                  >
                    启动
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void runAction('ios_shutdown_simulator', selected)}
                  >
                    关机
                  </button>
                </div>
              ) : null}
              {resultJson ? <div className="ios-json"><JsonPane raw={resultJson} /></div> : null}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
