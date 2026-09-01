import { useCallback, useEffect, useState } from 'react';
import { callTool } from '../api';
import { JsonPane } from '../components/JsonPane';
import {
  ANDROID_POLL_MS,
  EMPTY_ADB_MESSAGE,
  injectFailureMessage,
  isSelectedPresent,
  mergeDeviceLists,
  mergeDeviceModel,
  modelFromInfo,
  needsDeviceInfo,
  parseDevices,
  resultToJson,
  reverseProxyArgs,
  serialArgs,
  setupWifiProxyArgs,
  wifiProxyPort,
  type AndroidDevice,
  type ToolEnvelope,
} from './androidState';

type ListResult = ToolEnvelope & { devices?: unknown[] };
type InfoResult = ToolEnvelope & { device?: { model?: unknown } };

type AndroidProps = {
  proxyPort: number;
};

export default function Android({ proxyPort }: AndroidProps) {
  const [devices, setDevices] = useState<AndroidDevice[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [actionBanner, setActionBanner] = useState<string | null>(null);
  const [resultJson, setResultJson] = useState('');
  const [wifiHost, setWifiHost] = useState('');
  const [wifiPort, setWifiPort] = useState(String(proxyPort));
  const [busy, setBusy] = useState(false);

  const refreshDevices = useCallback(async () => {
    const result = await callTool<ListResult>('android_list_devices');
    const next = parseDevices(result);
    setDevices((prev) => mergeDeviceLists(prev, next));
    setLoaded(true);
    setSelected((cur) => (isSelectedPresent(next, cur) ? cur : null));
    setListError(result.success === false ? (result.message ?? 'android_list_devices failed') : null);
    return next;
  }, []);

  useEffect(() => {
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
          setListError(err instanceof Error ? err.message : 'android_list_devices failed');
          setLoaded(true);
        }
      } finally {
        inFlight = false;
        if (!cancelled) {
          timer = window.setTimeout(() => void tick(), ANDROID_POLL_MS);
        }
      }
    }

    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [refreshDevices]);

  async function selectDevice(serial: string) {
    setSelected(serial);
    const device = devices.find((row) => row.serial === serial);
    try {
      const info = await callTool<InfoResult>('android_get_device_info', serialArgs(serial));
      setResultJson(resultToJson(info));
      const model = modelFromInfo(info);
      if (device && needsDeviceInfo(device) && model) {
        setDevices((prev) => mergeDeviceModel(prev, serial, model));
      }
      if (info.success === false) {
        setActionBanner(info.message ?? 'android_get_device_info failed');
      } else {
        setActionBanner(null);
      }
    } catch (err) {
      setActionBanner(err instanceof Error ? err.message : 'android_get_device_info failed');
    }
  }

  async function runAction(name: string, args: Record<string, unknown>) {
    setBusy(true);
    try {
      const result = await callTool<ToolEnvelope>(name, args);
      setResultJson(resultToJson(result));
      if (name === 'android_inject_system_cert') {
        setActionBanner(injectFailureMessage(result));
      } else if (result.success === false) {
        setActionBanner(result.message ?? `${name} failed`);
      } else {
        setActionBanner(null);
      }
    } catch (err) {
      setActionBanner(err instanceof Error ? err.message : `${name} failed`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="android">
      <div className="page-toolbar">
        <span className="muted toolbar-count">{devices.length} 台</span>
      </div>
      {actionBanner || listError ? (
        <p className="page-banner warn-banner">{actionBanner ?? listError}</p>
      ) : null}
      <div className="traffic-split">
        <div className="traffic-list">
          {loaded && devices.length === 0 ? (
            <p className="empty-state muted">{EMPTY_ADB_MESSAGE}</p>
          ) : (
            <table className="traffic-table android-table">
              <thead>
                <tr>
                  <th>serial</th>
                  <th>state</th>
                  <th>model</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((device) => {
                  const active = device.serial === selected;
                  return (
                    <tr
                      key={device.serial}
                      className={active ? 'selected' : undefined}
                      onClick={() => void selectDevice(device.serial)}
                    >
                      <td className="mono">{device.serial}</td>
                      <td>{device.state || '—'}</td>
                      <td>{device.model || '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <aside className="inspector" aria-label="Android device">
          {!selected ? (
            <p className="muted empty-state">Select a device</p>
          ) : (
            <>
              <div className="android-actions">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void runAction('android_get_proxy', serialArgs(selected))}
                >
                  刷新代理
                </button>
                <button
                  className="primary"
                  type="button"
                  disabled={busy}
                  onClick={() => void runAction('android_reverse_proxy', reverseProxyArgs(selected, proxyPort))}
                >
                  Reverse 到本机
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void runAction('android_reverse_proxy_remove', reverseProxyArgs(selected, proxyPort))
                  }
                >
                  拆除 Reverse
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void runAction('android_clear_proxy', serialArgs(selected))}
                >
                  清除 Wi‑Fi 代理
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void runAction('android_cert_status', serialArgs(selected))}
                >
                  证书状态
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void runAction('android_push_cert', serialArgs(selected))}
                >
                  推用户证书
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void runAction('android_inject_system_cert', serialArgs(selected))}
                >
                  注入系统证书
                </button>
              </div>
              <div className="inspector-form">
                <p className="muted">手机 Wi‑Fi 代理，不是 Mac 系统代理</p>
                <div className="field-row">
                  <label className="field">
                    <span>host</span>
                    <input
                      value={wifiHost}
                      onChange={(event) => setWifiHost(event.target.value)}
                      placeholder="192.168.1.8"
                      aria-label="Wi-Fi proxy host"
                    />
                  </label>
                  <label className="field">
                    <span>port</span>
                    <input
                      type="number"
                      min={1}
                      value={wifiPort}
                      onChange={(event) => setWifiPort(event.target.value)}
                      aria-label="Wi-Fi proxy port"
                    />
                  </label>
                </div>
                <div className="form-actions">
                  <button
                    type="button"
                    disabled={busy || !wifiHost.trim()}
                    onClick={() =>
                      void runAction(
                        'android_setup_proxy',
                        setupWifiProxyArgs(selected, wifiHost.trim(), wifiProxyPort(wifiPort, proxyPort)),
                      )
                    }
                  >
                    Wi‑Fi 代理
                  </button>
                </div>
              </div>
              {resultJson ? <div className="android-json"><JsonPane raw={resultJson} /></div> : null}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
