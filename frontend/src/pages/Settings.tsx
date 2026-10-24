import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getToken } from '../api';
import Icon, { type IconName } from '../components/Icon';

const WS_BASE = import.meta.env.VITE_WS_URL || `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;

interface Device {
  id: number;
  name: string;
  channels?: number;
}

// Server close codes: 4401 = bad/expired JWT (don't retry), 1008 = rate limited.
const CLOSE_UNAUTHORIZED = 4401;
const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 30000;

interface Transcription {
  text: string;
  timestamp: string;
}

type Tone = 'green' | 'amber' | 'red' | 'blue';

function describe(connected: boolean, agentConnected: boolean, status: string): { title: string; text: string; tone: Tone; icon: IconName } {
  if (!connected) return { title: 'Нет связи с сервером', text: 'Переподключаемся…', tone: 'red', icon: 'plug' };
  if (!agentConnected) return { title: 'Агент не в сети', text: 'Запустите discord-task-agent.exe на компьютере.', tone: 'red', icon: 'monitor' };
  if (status === 'listening') return { title: 'Слушаю', text: 'Звук захватывается и распознаётся.', tone: 'green', icon: 'mic' };
  if (status === 'processing') return { title: 'Обработка', text: 'Распознаём фрагмент речи…', tone: 'blue', icon: 'wave' };
  if (status === 'idle') return { title: 'Ожидание', text: 'Выберите устройство и запустите захват.', tone: 'amber', icon: 'monitor' };
  return { title: status, text: '', tone: 'amber', icon: 'monitor' };
}

function ConnRow({ label, ok, okText, failText }: { label: string; ok: boolean; okText: string; failText: string }) {
  return (
    <div className="conn">
      <span className="muted">{label}</span>
      <span className="conn-state" style={{ ['--c' as string]: ok ? 'var(--green)' : 'var(--text-3)' }}>
        <span className="dot" />{ok ? okText : failText}
      </span>
    </div>
  );
}

export default function Agent() {
  const [connected, setConnected] = useState(false);
  const [agentConnected, setAgentConnected] = useState(false);
  const [agentStatus, setAgentStatus] = useState('idle');
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);
  const [transcriptions, setTranscriptions] = useState<Transcription[]>([]);
  const [agentUserId, setAgentUserId] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const feedRef = useRef<HTMLDivElement>(null);

  // Newest entries are on top: keep the feed pinned there as they arrive.
  useEffect(() => {
    feedRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
  }, [transcriptions]);

  useEffect(() => {
    let unmounted = false;
    let retryDelay = RECONNECT_MIN_MS;
    let retryTimer: number | null = null;

    const connect = () => {
      const token = getToken();
      if (!token) return;

      const ws = new WebSocket(`${WS_BASE}/api/agent/ws/frontend`);
      wsRef.current = ws;

      // Browsers can't set WebSocket headers; the JWT goes in the first message, never in the URL.
      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'auth', token }));
        setConnected(true);
        retryDelay = RECONNECT_MIN_MS;
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          switch (msg.type) {
            case 'status':
              setAgentConnected(!!msg.connected);
              setAgentStatus(msg.status);
              setSelectedDeviceId(msg.device_id ?? null);
              if (msg.devices) setDevices(msg.devices);
              setAgentUserId(msg.user_id ?? null);
              break;
            case 'devices':
              setDevices(msg.devices);
              if (msg.device_id != null) setSelectedDeviceId(msg.device_id);
              break;
            case 'transcription':
              setTranscriptions((prev) => [
                { text: msg.text, timestamp: msg.timestamp },
                ...prev,
              ].slice(0, 100));
              break;
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      ws.onclose = (event) => {
        setConnected(false);
        setAgentConnected(false);
        if (unmounted || event.code === CLOSE_UNAUTHORIZED) return;
        const delay = retryDelay * (0.5 + Math.random() / 2);
        retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS);
        retryTimer = window.setTimeout(connect, delay);
      };

      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      unmounted = true;
      if (retryTimer != null) window.clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, []);

  const sendCommand = (action: string, params?: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'command', action, params: params || {} }));
    }
  };

  const isListening = agentStatus === 'listening' || agentStatus === 'processing';
  const online = connected && agentConnected;
  const state = describe(connected, agentConnected, agentStatus);

  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 8 }}>
        <div>
          <h1 className="page-title">Агент</h1>
          <p className="page-sub">Управление захватом звука и живая расшифровка звонков.</p>
        </div>
      </div>

      <div className="grid grid-2">
        <section className="card card-body">
          <div className="status-panel">
            <span className={`status-orb orb-${state.tone}${online && isListening ? ' on' : ''}`}>
              <Icon name={state.icon} className="icon icon-lg" />
            </span>
            <div>
              <div className="status-title">{state.title}</div>
              <div className="muted" style={{ fontSize: 14 }}>{state.text}</div>
            </div>
          </div>

          {!agentConnected && connected && (
            <Link to="/download" className="btn btn-secondary btn-sm" style={{ marginTop: 18 }}>
              <Icon name="download" className="icon icon-sm" />Скачать агента
            </Link>
          )}
        </section>

        <section className="card card-body conn-list">
          <ConnRow label="Сервер" ok={connected} okText="Подключён" failText="Нет связи" />
          <ConnRow label="Desktop-агент" ok={agentConnected} okText="В сети" failText="Не в сети" />
          <ConnRow label="Аккаунт" ok={!!agentUserId} okText="Привязан" failText="Не привязан" />
          <ConnRow label="Устройства" ok={devices.length > 0} okText={`Найдено: ${devices.length}`} failText="Нет данных" />
        </section>
      </div>

      <section className="card card-body">
        <div className="controls">
          <div>
            <label className="field-label" htmlFor="device">Аудиоустройство</label>
            <select
              id="device"
              className="select"
              value={selectedDeviceId ?? ''}
              disabled={!online || devices.length === 0}
              onChange={(e) => {
                const id = Number(e.target.value);
                setSelectedDeviceId(id);
                sendCommand('select_device', { device_id: id });
              }}
            >
              <option value="" disabled>{devices.length ? 'Выберите устройство…' : 'Устройства появятся, когда агент подключится'}</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>{d.name}{d.channels != null ? ` · ${d.channels} кан.` : ''}</option>
              ))}
            </select>
          </div>

          <button
            onClick={() => sendCommand(isListening ? 'stop' : 'start')}
            disabled={!online || selectedDeviceId == null || devices.length === 0}
            className={`btn ${isListening ? 'btn-danger' : 'btn-primary'}`}
            style={{ minWidth: 170 }}
          >
            <Icon name={isListening ? 'stop' : 'play'} className="icon icon-sm" />
            {isListening ? 'Остановить захват' : 'Начать захват'}
          </button>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div className="card-title"><Icon name="wave" />Живая расшифровка</div>
          {online && isListening
            ? <span className="live">В эфире</span>
            : <span className="faint" style={{ fontSize: 13 }}>{transcriptions.length} фрагм.</span>}
        </div>
        <div className="feed" ref={feedRef}>
          {transcriptions.length === 0 ? (
            <div className="empty">
              <div className="empty-icon">
                {connected ? <Icon name="mic" className="icon icon-lg" /> : <span className="spinner" />}
              </div>
              <h3>{connected ? 'Пока тихо' : 'Подключение…'}</h3>
              <p>{connected ? 'Расшифровки появятся здесь, как только агент начнёт слушать.' : 'Устанавливаем соединение с сервером.'}</p>
            </div>
          ) : (
            transcriptions.map((t, i) => (
              <div key={`${t.timestamp}-${transcriptions.length - i}`} className="feed-item">
                <span className="feed-time">
                  {new Date(t.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                </span>
                <span className="feed-text">{t.text}</span>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
