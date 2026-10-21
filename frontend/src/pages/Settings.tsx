import { useEffect, useRef, useState, useCallback } from 'react';

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

export default function Agent() {
  const [connected, setConnected] = useState(false);
  const [agentConnected, setAgentConnected] = useState(false);
  const [agentStatus, setAgentStatus] = useState('idle');
  const [devices, setDevices] = useState<Device[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);
  const [transcriptions, setTranscriptions] = useState<Transcription[]>([]);
  const [agentUserId, setAgentUserId] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const retryDelayRef = useRef(RECONNECT_MIN_MS);
  const retryTimerRef = useRef<number | null>(null);
  const unmountedRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcriptions]);

  const connect = useCallback(() => {
    const token = localStorage.getItem('jwt_token');
    if (!token) return;

    const ws = new WebSocket(`${WS_BASE}/api/agent/ws/frontend`);
    wsRef.current = ws;

    // Browsers can't set WebSocket headers; the JWT goes in the first message, never in the URL.
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token }));
      setConnected(true);
      retryDelayRef.current = RECONNECT_MIN_MS;
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
      } catch {}
    };

    ws.onclose = (event) => {
      setConnected(false);
      setAgentConnected(false);
      if (unmountedRef.current || event.code === CLOSE_UNAUTHORIZED) return;
      const delay = retryDelayRef.current * (0.5 + Math.random() / 2);
      retryDelayRef.current = Math.min(retryDelayRef.current * 2, RECONNECT_MAX_MS);
      retryTimerRef.current = window.setTimeout(connect, delay);
    };

    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    unmountedRef.current = false;
    connect();
    return () => {
      unmountedRef.current = true;
      if (retryTimerRef.current != null) window.clearTimeout(retryTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const sendCommand = (action: string, params?: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'command', action, params: params || {} }));
    }
  };

  const isListening = agentStatus === 'listening' || agentStatus === 'processing';

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700 shadow-sm">
        <h2 className="text-xl font-bold mb-4">Desktop Agent</h2>

        <div className="flex items-center gap-4 mb-6 bg-gray-900/50 rounded-lg p-4 border border-gray-700">
          <div className={`w-3.5 h-3.5 rounded-full ${
            connected && agentConnected ? (isListening ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]' : 'bg-yellow-500 shadow-[0_0_8px_rgba(234,179,8,0.5)]') : 'bg-red-500'
          }`} />
          <div>
            <span className="text-sm font-medium">
              {!connected ? 'Disconnected' : !agentConnected ? 'Agent offline' : agentStatus === 'idle' ? 'Idle' : agentStatus === 'listening' ? 'Listening...' : agentStatus === 'processing' ? 'Processing...' : agentStatus}
            </span>
            {agentUserId && (
              <span className="text-xs text-green-400 ml-3">✓ User linked</span>
            )}
          </div>
        </div>

        <div className="flex items-end gap-3">
          <div className="flex-1">
            <label className="text-xs text-gray-400 mb-1 block">Audio device</label>
            <select
              className="bg-gray-700 border border-gray-600 rounded-lg px-3 py-2.5 text-sm w-full"
              value={selectedDeviceId ?? ''}
              onChange={(e) => {
                const id = Number(e.target.value);
                setSelectedDeviceId(id);
                sendCommand('select_device', { device_id: id });
              }}
            >
              <option value="" disabled>Select audio device...</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>{d.name}{d.channels != null ? ` (${d.channels} ch)` : ''}</option>
              ))}
            </select>
          </div>

          <button
            onClick={() => sendCommand(isListening ? 'stop' : 'start')}
            disabled={!connected || !agentConnected || selectedDeviceId == null || devices.length === 0}
            className={`px-6 py-2.5 rounded-lg text-sm font-semibold transition shadow-sm ${
              isListening
                ? 'bg-red-600 hover:bg-red-500 text-white'
                : 'bg-indigo-600 hover:bg-indigo-500 text-white'
            } disabled:opacity-40 disabled:cursor-not-allowed`}
          >
            {isListening ? 'Stop Capture' : 'Start Capture'}
          </button>
        </div>
      </div>

      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700 shadow-sm">
        <h3 className="text-lg font-bold mb-3">Live Transcriptions</h3>

        <div className="bg-gray-900/50 rounded-lg h-96 overflow-y-auto space-y-2 p-3 border border-gray-700">
          {transcriptions.length === 0 ? (
            <p className="text-gray-500 text-sm text-center mt-20">
              {!connected ? 'Connecting...' : 'No transcriptions yet. Start the agent.'}
            </p>
          ) : (
            transcriptions.map((t, i) => (
              <div key={i} className="bg-gray-800 rounded-lg px-4 py-3 border border-gray-700">
                <div className="text-gray-500 text-xs mb-1">
                  {new Date(t.timestamp).toLocaleTimeString()}
                </div>
                <div className="text-sm leading-relaxed">{t.text}</div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  );
}
