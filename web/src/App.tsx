import { useCallback, useEffect, useRef, useState } from 'react';
import { callTool, ensureSession, getJson } from './api';
import Android from './pages/Android';
import Ios from './pages/Ios';
import Mock from './pages/Mock';
import Proxy from './pages/Proxy';
import { resolveAndroidProxyPort } from './pages/androidState';
import {
  DEFAULT_PROXY_HOST,
  DEFAULT_PROXY_PORT,
  DEVICE_PROXY_NOTE,
  HEALTH_POLL_MS,
  formatProxyListen,
  isDeviceTarget,
  isSessionExpired,
  mergeProxyView,
  nextUnauthorizedStreak,
  SESSION_EXPIRED_MESSAGE,
  setupProxyAllowed,
  startArgs,
  type HealthInfo,
  type RuntimeInfo,
} from './pages/proxyState';
import Traffic from './pages/Traffic';

const NAV = ['Traffic', 'Mock', 'Android', 'iOS', 'Proxy'] as const;

type NavId = (typeof NAV)[number];

function NavIcon({ id }: { id: NavId }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: '0 0 16 16',
    'aria-hidden': true,
  } as const;
  if (id === 'Traffic') {
    return (
      <svg {...common}>
        <path fill="currentColor" d="M2 3.5h12v1.25H2zm0 4h12v1.25H2zm0 4h8v1.25H2z" />
      </svg>
    );
  }
  if (id === 'Mock') {
    return (
      <svg {...common}>
        <path fill="currentColor" d="M3 8.5 9.5 2l-.75 5H13L6.5 14l.75-5.5z" />
      </svg>
    );
  }
  if (id === 'Android') {
    return (
      <svg {...common}>
        <path
          fill="currentColor"
          d="M5.2 3.4 4.4 2.1l.9-.5.7 1.2a5 5 0 0 1 4 0l.7-1.2.9.5-.8 1.3A4.2 4.2 0 0 1 12.2 7H3.8a4.2 4.2 0 0 1 1.4-3.6zM6 5.2h.9V6H6zm3.1 0H10V6H9.1zM3.5 8h9v5.2H12V14H11v-.8H5V14H4v-.8H3.5z"
        />
      </svg>
    );
  }
  if (id === 'iOS') {
    return (
      <svg {...common}>
        <path
          fill="currentColor"
          d="M5 1.5h6A1.5 1.5 0 0 1 12.5 3v10a1.5 1.5 0 0 1-1.5 1.5H5A1.5 1.5 0 0 1 3.5 13V3A1.5 1.5 0 0 1 5 1.5zm0 1.25A.25.25 0 0 0 4.75 3v10c0 .14.11.25.25.25h6c.14 0 .25-.11.25-.25V3a.25.25 0 0 0-.25-.25H8.2a.6.6 0 0 1-.4.7H8.2a.6.6 0 0 1-.4-.7z"
        />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path
        fill="currentColor"
        d="M8 1.5A6.5 6.5 0 1 1 1.5 8 6.5 6.5 0 0 1 8 1.5zm0 1.25a5.25 5.25 0 1 0 5.25 5.25A5.25 5.25 0 0 0 8 2.75zM8 5a1.2 1.2 0 1 1-1.2 1.2A1.2 1.2 0 0 1 8 5zm0 3.1c1.4 0 2.6.6 3.3 1.5l-.9.7C9.9 9.6 9 9.25 8 9.25s-1.9.35-2.4 1.05l-.9-.7C7.4 8.7 8.6 8.1 8 8.1z"
      />
    </svg>
  );
}

type ToolEnvelope = {
  success?: boolean;
  message?: string;
};

export default function App() {
  const [page, setPage] = useState<NavId>('Traffic');
  const [focusMockId, setFocusMockId] = useState<string | null>(null);
  const [sessionReady, setSessionReady] = useState(false);
  const [running, setRunning] = useState(false);
  const [runtimePort, setRuntimePort] = useState(DEFAULT_PROXY_PORT);
  const [listenHost, setListenHost] = useState(DEFAULT_PROXY_HOST);
  const [portInput, setPortInput] = useState(DEFAULT_PROXY_PORT);
  const [captureTarget, setCaptureTarget] = useState('');
  const [setupProxy, setSetupProxy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [actionBanner, setActionBanner] = useState<string | null>(null);
  const portTouchedRef = useRef(false);
  const runtimePortRef = useRef(runtimePort);
  const listenHostRef = useRef(listenHost);
  const captureTargetRef = useRef(captureTarget);
  runtimePortRef.current = runtimePort;
  listenHostRef.current = listenHost;
  captureTargetRef.current = captureTarget;

  useEffect(() => {
    let cancelled = false;
    void ensureSession()
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setSessionReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshStatus = useCallback(async (streak: number): Promise<number> => {
    const health = await getJson<HealthInfo>('/v1/health');
    const next = nextUnauthorizedStreak(streak, health.status);
    if (isSessionExpired(next)) {
      setSessionExpired(true);
      return next;
    }
    setSessionExpired(false);
    if (!health.data) return next;
    const runtime = await getJson<RuntimeInfo>('/v1/runtime');
    if (runtime.status === 401) {
      const runtimeStreak = nextUnauthorizedStreak(next, 401);
      if (isSessionExpired(runtimeStreak)) setSessionExpired(true);
      return runtimeStreak;
    }
    const view = mergeProxyView(health.data, runtime.data, {
      host: listenHostRef.current,
      port: runtimePortRef.current,
      captureTarget: captureTargetRef.current,
    });
    setRunning(view.running);
    setListenHost(view.host);
    setRuntimePort(view.port);
    setCaptureTarget(view.captureTarget);
    if (!portTouchedRef.current) setPortInput(view.port);
    return next;
  }, []);

  useEffect(() => {
    if (!sessionReady) return;
    let cancelled = false;
    let streak = 0;
    let timer = 0;
    let inFlight = false;

    async function tick() {
      if (cancelled || inFlight) return;
      inFlight = true;
      try {
        streak = await refreshStatus(streak);
      } catch {
        if (!cancelled) setActionBanner('control unavailable');
      } finally {
        inFlight = false;
        if (!cancelled) {
          timer = window.setTimeout(() => void tick(), HEALTH_POLL_MS);
        }
      }
    }

    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [sessionReady, refreshStatus]);

  const device = isDeviceTarget(captureTarget);

  async function onStart() {
    setBusy(true);
    setActionBanner(null);
    try {
      const result = await callTool<ToolEnvelope>('proxy_start', startArgs(portInput, setupProxy, captureTarget));
      if (result.success === false) {
        setActionBanner(result.message ?? 'proxy_start failed');
      }
      await refreshStatus(0);
    } catch (err) {
      setActionBanner(err instanceof Error ? err.message : 'proxy_start failed');
    } finally {
      setBusy(false);
    }
  }

  async function onStop() {
    setBusy(true);
    setActionBanner(null);
    try {
      const result = await callTool<ToolEnvelope>('proxy_stop', { port: portInput });
      if (result.success === false) {
        setActionBanner(result.message ?? 'proxy_stop failed');
      }
      await refreshStatus(0);
    } catch (err) {
      setActionBanner(err instanceof Error ? err.message : 'proxy_stop failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">Mitm</div>
        <nav aria-label="Workspace">
          {NAV.map((item) => {
            const active = page === item;
            return (
              <button
                key={item}
                type="button"
                className={active ? 'nav-item active' : 'nav-item'}
                aria-current={active ? 'page' : undefined}
                onClick={() => setPage(item)}
              >
                <NavIcon id={item} />
                {item}
              </button>
            );
          })}
        </nav>
      </aside>
      <div className="main">
        <header className="topbar">
          <span className={running ? 'status-dot on' : 'status-dot'} aria-hidden="true" />
          <span className="mono" title="代理监听地址">
            {formatProxyListen(listenHost, runtimePort)}
          </span>
          <span className="muted">{captureTarget || '—'}</span>
          <input
            className="toolbar-input topbar-port"
            type="number"
            min={1}
            aria-label="proxy port"
            value={portInput}
            onChange={(event) => {
              portTouchedRef.current = true;
              setPortInput(Number(event.target.value) || DEFAULT_PROXY_PORT);
            }}
          />
          <label className="toolbar-check">
            <input
              type="checkbox"
              checked={setupProxyAllowed(captureTarget) && setupProxy}
              disabled={device}
              onChange={(event) => setSetupProxy(event.target.checked)}
            />
            setup_proxy
          </label>
          {device ? <span className="muted">{DEVICE_PROXY_NOTE}</span> : null}
          <button className="primary" type="button" disabled={busy || sessionExpired || running} onClick={() => void onStart()}>
            启动
          </button>
          <button type="button" disabled={busy || sessionExpired || !running} onClick={() => void onStop()}>
            停止
          </button>
        </header>
        {sessionExpired ? <p className="page-banner warn-banner">{SESSION_EXPIRED_MESSAGE}</p> : null}
        {actionBanner && !sessionExpired ? <p className="page-banner warn-banner">{actionBanner}</p> : null}
        <section className="workspace" aria-label={page}>
          {!sessionReady ? (
            <p className="workspace-placeholder muted">Connecting…</p>
          ) : page === 'Traffic' ? (
            <Traffic
              onOpenMock={(id) => {
                setFocusMockId(id);
                setPage('Mock');
              }}
            />
          ) : page === 'Mock' ? (
            <Mock
              focusMockId={focusMockId}
              onFocusConsumed={() => setFocusMockId(null)}
            />
          ) : page === 'Android' ? (
            <Android proxyPort={resolveAndroidProxyPort(running, runtimePort, portInput)} />
          ) : page === 'iOS' ? (
            <Ios />
          ) : page === 'Proxy' ? (
            <Proxy />
          ) : (
            <p className="workspace-placeholder muted">{page}</p>
          )}
        </section>
      </div>
    </div>
  );
}
