import { describe, expect, it } from 'vitest';
import {
  certDisplayText,
  DEFAULT_PROXY_HOST,
  DEFAULT_PROXY_PORT,
  DEVICE_PROXY_NOTE,
  formatProxyListen,
  isDeviceTarget,
  isSessionExpired,
  mergeProxyView,
  nextUnauthorizedStreak,
  resolveHost,
  resolvePort,
  SESSION_401_LIMIT,
  SESSION_EXPIRED_MESSAGE,
  setupProxyAllowed,
  startArgs,
} from './proxyState';

describe('resolvePort', () => {
  it('uses runtime port when positive', () => {
    expect(resolvePort(9999)).toBe(9999);
  });

  it('falls back for missing or invalid port', () => {
    expect(resolvePort(undefined)).toBe(DEFAULT_PROXY_PORT);
    expect(resolvePort(0)).toBe(DEFAULT_PROXY_PORT);
    expect(resolvePort(-1)).toBe(DEFAULT_PROXY_PORT);
    expect(resolvePort('8888')).toBe(DEFAULT_PROXY_PORT);
  });
});

describe('formatProxyListen', () => {
  it('joins host and port', () => {
    expect(formatProxyListen('192.168.1.8', 8888)).toBe('192.168.1.8:8888');
  });

  it('falls back to 127.0.0.1 when host is empty', () => {
    expect(resolveHost('')).toBe(DEFAULT_PROXY_HOST);
    expect(formatProxyListen('', 9999)).toBe('127.0.0.1:9999');
  });
});

describe('setup_proxy device guard', () => {
  it('disables setup_proxy when capture_target is device', () => {
    expect(isDeviceTarget('device')).toBe(true);
    expect(setupProxyAllowed('device')).toBe(false);
    expect(startArgs(8888, true, 'device')).toEqual({
      port: 8888,
      setup_proxy: false,
      open_ui: true,
    });
  });

  it('allows setup_proxy for mac and simulator', () => {
    expect(setupProxyAllowed('mac')).toBe(true);
    expect(startArgs(9999, true, 'mac')).toEqual({
      port: 9999,
      setup_proxy: true,
      open_ui: true,
    });
    expect(startArgs(8888, false, 'simulator')).toEqual({
      port: 8888,
      setup_proxy: false,
      open_ui: true,
    });
  });

  it('keeps the device note string', () => {
    expect(DEVICE_PROXY_NOTE).toBe('真机禁止系统代理');
  });
});

describe('401 streak', () => {
  it('expires after three consecutive 401s', () => {
    let streak = 0;
    streak = nextUnauthorizedStreak(streak, 401);
    expect(isSessionExpired(streak)).toBe(false);
    streak = nextUnauthorizedStreak(streak, 401);
    expect(isSessionExpired(streak)).toBe(false);
    streak = nextUnauthorizedStreak(streak, 401);
    expect(streak).toBe(SESSION_401_LIMIT);
    expect(isSessionExpired(streak)).toBe(true);
    expect(SESSION_EXPIRED_MESSAGE).toBe('会话失效，刷新页面');
  });

  it('resets on a successful status', () => {
    let streak = nextUnauthorizedStreak(2, 401);
    expect(isSessionExpired(streak)).toBe(true);
    streak = nextUnauthorizedStreak(streak, 200);
    expect(streak).toBe(0);
    expect(isSessionExpired(streak)).toBe(false);
  });
});

describe('mergeProxyView', () => {
  it('reads running from health and port/target from runtime', () => {
    expect(
      mergeProxyView({ proxy_running: true }, { proxy_port: 9999, capture_target: 'mac' }),
    ).toEqual({ running: true, host: DEFAULT_PROXY_HOST, port: 9999, captureTarget: 'mac' });
  });

  it('defaults port and empty target on first frame', () => {
    expect(mergeProxyView(null, null)).toEqual({
      running: false,
      host: DEFAULT_PROXY_HOST,
      port: DEFAULT_PROXY_PORT,
      captureTarget: '',
    });
  });

  it('keeps previous target and port when runtime is missing', () => {
    const previous = { host: DEFAULT_PROXY_HOST, port: 9999, captureTarget: 'device' };
    const next = mergeProxyView({ proxy_running: true }, null, previous);
    expect(next).toEqual({
      running: true,
      host: DEFAULT_PROXY_HOST,
      port: 9999,
      captureTarget: 'device',
    });
    expect(setupProxyAllowed(next.captureTarget)).toBe(false);
  });

  it('keeps previous target when runtime omits capture_target', () => {
    expect(
      mergeProxyView(
        { proxy_running: false },
        { proxy_port: 7777 },
        { port: 9999, captureTarget: 'device', host: '10.0.0.2' },
      ),
    ).toEqual({ running: false, host: '10.0.0.2', port: 7777, captureTarget: 'device' });
  });

  it('updates target and port when runtime is present', () => {
    expect(
      mergeProxyView(
        { proxy_running: true, proxy_host: '192.168.1.8' },
        { proxy_port: 7777, capture_target: 'mac' },
        { port: 9999, captureTarget: 'device', host: DEFAULT_PROXY_HOST },
      ),
    ).toEqual({ running: true, host: '192.168.1.8', port: 7777, captureTarget: 'mac' });
  });
});

describe('certDisplayText', () => {
  it('prefers install_instructions as original text', () => {
    expect(
      certDisplayText({
        success: true,
        install_instructions: '## mitmproxy CA\nVisit http://mitm.it',
      }),
    ).toBe('## mitmproxy CA\nVisit http://mitm.it');
  });

  it('prepends message when cert is missing', () => {
    expect(
      certDisplayText({
        success: false,
        message: 'CA 证书未找到',
        install_instructions: 'Start proxy first',
      }),
    ).toBe('CA 证书未找到\n\nStart proxy first');
  });

  it('stringifies when instructions are absent', () => {
    expect(certDisplayText({ success: false, detail: 'oops' })).toBe(
      JSON.stringify({ success: false, detail: 'oops' }, null, 2),
    );
  });
});
