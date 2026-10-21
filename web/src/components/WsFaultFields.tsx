type Value = string | number | boolean | undefined;

type Props = {
  config: Record<string, unknown>;
  disabled: boolean;
  onChange: (key: string, value: Value) => void;
};

// Edit the persisted fault configuration directly so UI and MCP share semantics.
//
// 直接编辑持久化的故障配置，让页面与 MCP 的触发、方向和动作语义保持一致。
export function WsFaultFields({ config, disabled, onChange }: Props) {
  const action = String(config.action ?? 'pause');
  const timed = config.trigger === 'time';
  return (
    <>
      <div className="field-row">
        <label className="field">
          <span>作用方向</span>
          <select
            disabled={disabled}
            value={String(config.direction ?? 'server')}
            onChange={(e) => onChange('direction', e.target.value)}
          >
            <option value="server">服务端 → 客户端</option>
            <option value="client">客户端 → 服务端</option>
            <option value="both">双向</option>
          </select>
        </label>
        <label className="field">
          <span>触发条件</span>
          <select
            disabled={disabled}
            value={timed ? 'time' : 'messages'}
            onChange={(e) => onChange('trigger', e.target.value)}
          >
            <option value="messages">经过指定消息条数</option>
            <option value="time">连接建立后计时</option>
          </select>
        </label>
      </div>
      <label className="field">
        <span>{timed ? '建连后等待（毫秒）' : '先正常通过的消息条数'}</span>
        <input
          disabled={disabled}
          type="number"
          min={0}
          value={Number(config[timed ? 'after_ms' : 'after_messages'] ?? 0)}
          onChange={(e) => onChange(timed ? 'after_ms' : 'after_messages', Number(e.target.value))}
        />
      </label>
      <label className="field">
        <span>故障动作</span>
        <select
          disabled={disabled}
          value={action}
          onChange={(e) => onChange('action', e.target.value)}
        >
          <option value="delay">原样延迟 / 随机卡顿</option>
          <option value="pause">暂停后恢复</option>
          <option value="close">发送关闭帧</option>
          <option value="abort">突然断开连接</option>
        </select>
      </label>
      {action === 'pause' ? (
        <label className="field">
          <span>暂停时长（毫秒）</span>
          <input
            disabled={disabled}
            type="number"
            min={1}
            value={Number(config.duration_ms ?? 10000)}
            onChange={(e) => onChange('duration_ms', Number(e.target.value))}
          />
        </label>
      ) : null}
      {action === 'delay' ? (
        <label className="field">
          <span>随机抖动范围 ± 毫秒（基础延迟使用上方 Delay ms）</span>
          <input
            disabled={disabled}
            type="number"
            min={0}
            value={Number(config.jitter_ms ?? 0)}
            onChange={(e) => onChange('jitter_ms', Number(e.target.value))}
          />
        </label>
      ) : null}
      <div className="field-row">
        <label className="field">
          <span>触发概率（0–1）</span>
          <input
            disabled={disabled}
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={Number(config.probability ?? 1)}
            onChange={(e) => onChange('probability', Number(e.target.value))}
          />
        </label>
        <label className="field">
          <span>随机种子（可选）</span>
          <input
            disabled={disabled}
            type="number"
            min={0}
            max={4294967295}
            placeholder="每次随机"
            value={config.seed == null ? '' : Number(config.seed)}
            onChange={(e) =>
              onChange('seed', e.target.value === '' ? undefined : Number(e.target.value))
            }
          />
        </label>
      </div>
      <label className="field-check">
        <input
          disabled={disabled}
          type="checkbox"
          checked={config.preserve_heartbeat !== false}
          onChange={(e) => onChange('preserve_heartbeat', e.target.checked)}
        />
        保留 Socket.IO 建立连接与心跳消息
      </label>
      {action === 'close' ? (
        <>
          <label className="field">
            <span>关闭码</span>
            <input
              disabled={disabled}
              type="number"
              value={Number(config.close_code ?? 1000)}
              onChange={(e) => onChange('close_code', Number(e.target.value))}
            />
          </label>
          <label className="field">
            <span>关闭原因</span>
            <input
              disabled={disabled}
              value={String(config.close_reason ?? 'mock fault')}
              onChange={(e) => onChange('close_reason', e.target.value)}
            />
          </label>
        </>
      ) : null}
      <p className="muted">
        暂停、关闭和中断每条连接最多执行一次；延迟按消息概率触发。关闭与中断结束整条连接，条数仍按所选方向统计。延迟和暂停保留文本及二进制消息；原生
        WebSocket ping/pong 控制帧始终透传。队列超过 1000 条或 8 MiB 时以 1009
        关闭，防止内存持续增长。
      </p>
    </>
  );
}
