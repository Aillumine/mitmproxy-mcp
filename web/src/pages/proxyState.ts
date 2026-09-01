export const DEFAULT_PROXY_PORT = 8888;
export const DEFAULT_PROXY_HOST = '127.0.0.1';
export const HEALTH_POLL_MS = 3000;
export const SESSION_401_LIMIT = 3;
export const SESSION_EXPIRED_MESSAGE = '会话失效，刷新页面';
export const DEVICE_PROXY_NOTE = '真机禁止系统代理';

export type HealthInfo = {
  ok?: boolean;
  proxy_running?: boolean;
  proxy_host?: string;
};

export type RuntimeInfo = {
  proxy_port?: number;
  capture_target?: string;
};

export type ProxyView = {
  running: boolean;
  host: string;
  port: number;
  captureTarget: string;
};

export function resolvePort(runtimePort: unknown, fallback = DEFAULT_PROXY_PORT): number {
  return typeof runtimePort === 'number' && Number.isFinite(runtimePort) && runtimePort > 0
    ? Math.trunc(runtimePort)
    : fallback;
}

export function resolveHost(host: unknown, fallback = DEFAULT_PROXY_HOST): string {
  return typeof host === 'string' && host.trim() ? host.trim() : fallback;
}

export function formatProxyListen(host: string, port: number): string {
  return `${resolveHost(host)}:${resolvePort(port)}`;
}

export function isDeviceTarget(target: string): boolean {
  return target === 'device';
}

export function setupProxyAllowed(target: string): boolean {
  return !isDeviceTarget(target);
}

export function nextUnauthorizedStreak(prev: number, status: number): number {
  return status === 401 ? prev + 1 : 0;
}

export function isSessionExpired(streak: number, limit = SESSION_401_LIMIT): boolean {
  return streak >= limit;
}

export function mergeProxyView(
  health: HealthInfo | null,
  runtime: RuntimeInfo | null,
  previous: Pick<ProxyView, 'host' | 'port' | 'captureTarget'> = {
    host: DEFAULT_PROXY_HOST,
    port: DEFAULT_PROXY_PORT,
    captureTarget: '',
  },
): ProxyView {
  const port = runtime
    ? resolvePort(runtime.proxy_port, previous.port)
    : previous.port;
  const captureTarget =
    runtime && typeof runtime.capture_target === 'string'
      ? runtime.capture_target
      : previous.captureTarget;
  return {
    running: Boolean(health?.proxy_running),
    host: resolveHost(health?.proxy_host, previous.host),
    port,
    captureTarget,
  };
}

export function startArgs(
  port: number,
  setupProxy: boolean,
  captureTarget: string,
): { port: number; setup_proxy: boolean; open_ui: true } {
  return {
    port: resolvePort(port),
    setup_proxy: setupProxyAllowed(captureTarget) && setupProxy,
    open_ui: true,
  };
}

export function certDisplayText(result: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof result.message === 'string' && result.message) {
    parts.push(result.message);
  }
  if (typeof result.install_instructions === 'string' && result.install_instructions) {
    parts.push(result.install_instructions);
  }
  if (parts.length > 0) return parts.join('\n\n');
  return JSON.stringify(result, null, 2);
}
