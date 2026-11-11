import { useEffect, useRef, useState } from "react";
import { getJson } from "../api";
import { formatProxyListen, HEALTH_POLL_MS } from "./proxyState";

type Address = { interface: string; label: string; host: string; kind: string };
export type AddressState = {
  addresses: Address[];
  selected_interface: string | null;
  active_interface: string | null;
  host: string | null;
  warning: string | null;
};

export function AddressOptions({
  state,
  port,
}: {
  state: AddressState | null;
  port: number;
}) {
  return (
    <>
      <option value="">自动（优先 Wi-Fi）</option>
      {state?.selected_interface &&
      !state.addresses.some(
        (item) => item.interface === state.selected_interface,
      ) ? (
        <option value={state.selected_interface}>
          {state.selected_interface}（已断开）
        </option>
      ) : null}
      {state?.addresses.map((item) => (
        <option key={`${item.interface}-${item.host}`} value={item.interface}>
          {item.label} · {formatProxyListen(item.host, port)}
        </option>
      ))}
    </>
  );
}

export default function ProxyAddress({
  port,
  onHost,
}: {
  port: number;
  onHost: (host: string) => void;
}) {
  const [state, setState] = useState<AddressState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copiedAddress, setCopiedAddress] = useState<string | null>(null);
  const revision = useRef(0);
  const saving = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    async function refresh() {
      const version = revision.current;
      try {
        if (!saving.current) {
          const result = await getJson<AddressState>("/v1/ui/proxy-address");
          // Ignore a poll started before a manual selection; stale replies must
          // not undo the freshly saved preference in the picker.
          //
          // 手动选择期间忽略较早发出的轮询结果，避免旧响应覆盖刚保存的偏好。
          if (!cancelled && version === revision.current) {
            if (!result.data)
              throw new Error("无法读取设备连接地址，请刷新重试");
            setState(result.data);
            setError(null);
          }
        }
      } catch (err) {
        if (!cancelled && version === revision.current)
          setError(err instanceof Error ? err.message : "连接地址刷新失败");
      } finally {
        if (!cancelled)
          timer = window.setTimeout(() => void refresh(), HEALTH_POLL_MS);
      }
    }
    void refresh();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  async function select(interfaceName: string) {
    revision.current++;
    saving.current = true;
    setBusy(true);
    setCopiedAddress(null);
    try {
      const response = await fetch("/v1/ui/proxy-address", {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interface: interfaceName || null }),
      });
      const result = await response.json();
      if (!response.ok)
        throw new Error(
          typeof result.detail === "string"
            ? result.detail
            : "连接地址保存失败",
        );
      setState(result as AddressState);
      onHost(result.host ?? "127.0.0.1");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "连接地址保存失败");
    } finally {
      saving.current = false;
      setBusy(false);
    }
  }

  const unavailable = !state?.host || !!error;
  const address = state?.host ? formatProxyListen(state.host, port) : null;
  return (
    <div className="proxy-address-picker">
      <label>
        设备连接地址{" "}
        <select
          aria-label="设备连接地址"
          disabled={busy || !state}
          value={state?.selected_interface ?? ""}
          onChange={(event) => void select(event.target.value)}
        >
          <AddressOptions state={state} port={port} />
        </select>
      </label>
      <span className="mono">
        {!state
          ? "读取中…"
          : unavailable
            ? "地址不可用"
            : formatProxyListen(state.host!, port)}
      </span>
      <button
        type="button"
        disabled={unavailable}
        onClick={() => {
          void navigator.clipboard
            .writeText(address!)
            .then(() => setCopiedAddress(address))
            .catch(() => setError("复制失败，请手动复制连接地址"));
        }}
      >
        {copiedAddress && copiedAddress === address ? "已复制" : "复制地址"}
      </button>
      {error || state?.warning ? (
        <span role="status" className="warn-banner">
          {error || state?.warning}
        </span>
      ) : null}
    </div>
  );
}
