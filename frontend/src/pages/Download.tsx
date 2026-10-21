import { useEffect, useState } from 'react';
import { getMe } from '../api';

const API_BASE = import.meta.env.VITE_API_URL || '';

export default function Download() {
  const [discordId, setDiscordId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingCopied, setPairingCopied] = useState(false);
  const [pairingLoading, setPairingLoading] = useState(false);
  const [pairingError, setPairingError] = useState('');
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    getMe().then((user) => {
      setDiscordId(user.discord_id);
    }).catch(() => {});
  }, []);

  const downloadAgent = async () => {
    setDownloading(true);
    try {
      const token = localStorage.getItem('jwt_token');
      const resp = await fetch(`${API_BASE}/api/agent/download`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!resp.ok) throw new Error('Download failed');
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'discord-task-agent.zip';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setPairingError(e.message || 'Failed to download');
    } finally {
      setDownloading(false);
    }
  };

  const generatePairingCode = async () => {
    setPairingLoading(true);
    setPairingError('');
    try {
      const resp = await fetch(`${API_BASE}/api/pairing/code`, {
        headers: { 'Authorization': `Bearer ${localStorage.getItem('jwt_token')}` },
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to generate code');
      }
      const data = await resp.json();
      setPairingCode(data.code);
    } catch (e: any) {
      setPairingError(e.message || 'Failed to generate pairing code');
    } finally {
      setPairingLoading(false);
    }
  };

  const copyPairingCode = async () => {
    if (pairingCode) {
      await navigator.clipboard.writeText(pairingCode);
      setPairingCopied(true);
      setTimeout(() => setPairingCopied(false), 2000);
    }
  };

  const handleCopyId = async () => {
    if (discordId) {
      await navigator.clipboard.writeText(discordId);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700 shadow-sm">
        <h2 className="text-xl font-bold mb-2">Desktop Agent</h2>
        <p className="text-gray-400 text-sm mb-6">
          Records desktop audio during Discord voice calls, transcribes speech, and creates tasks.
        </p>

        <div className="bg-gray-900/50 rounded-lg p-4 mb-6 border border-gray-700">
          <p className="text-sm text-gray-400 mb-2">Your Discord ID:</p>
          <div className="flex items-center gap-2">
            <code className="text-base text-indigo-300 font-mono bg-gray-800 px-3 py-2 rounded flex-1 border border-gray-600 truncate">
              {discordId || 'Loading...'}
            </code>
            <button
              onClick={handleCopyId}
              disabled={!discordId}
              className="px-4 py-2 rounded text-sm font-medium bg-gray-700 hover:bg-gray-600 disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>

        <div className="flex flex-wrap gap-3 mb-4">
          <button
            onClick={downloadAgent}
            disabled={downloading}
            className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-6 py-3 rounded-lg transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {downloading ? (
              <>
                <svg className="animate-spin w-5 h-5" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                </svg>
                Downloading...
              </>
            ) : (
              <>
                <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                </svg>
                Download Agent (Windows)
              </>
            )}
          </button>
        </div>

        <p className="text-xs text-gray-500">
          ✓ Already includes your Discord ID — just extract and run
        </p>
        <p className="text-xs text-gray-500 mt-1">
          ~30 MB + ~1.5 GB Whisper model (downloaded on first launch)
        </p>
      </div>

      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700 shadow-sm">
        <h3 className="text-lg font-bold mb-2">Pairing Code</h3>
        <p className="text-gray-400 text-sm mb-4">
          Alternative: on first launch the agent will ask for a code. Generate one below and enter it in the agent terminal.
        </p>

        {pairingCode ? (
          <div className="flex items-center gap-3 bg-gray-900/50 rounded-lg p-4 border border-gray-700">
            <code className="text-2xl text-indigo-300 font-mono bg-gray-800 px-4 py-2 rounded tracking-widest border border-gray-600">
              {pairingCode}
            </code>
            <button
              onClick={copyPairingCode}
              className="px-4 py-2 rounded text-sm font-medium bg-gray-700 hover:bg-gray-600 transition"
            >
              {pairingCopied ? 'Copied!' : 'Copy'}
            </button>
            <span className="text-xs text-gray-500">valid for 10 min</span>
          </div>
        ) : (
          <div>
            <button
              onClick={generatePairingCode}
              disabled={pairingLoading}
              className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-6 py-3 rounded-lg transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {pairingLoading ? (
                <>
                  <svg className="animate-spin w-4 h-4" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                  </svg>
                  Generating...
                </>
              ) : (
                'Generate Code'
              )}
            </button>
            {pairingError && (
              <p className="text-red-400 text-sm mt-2">{pairingError}</p>
            )}
          </div>
        )}
      </div>

      <div className="bg-gray-800 rounded-xl p-6 border border-gray-700 shadow-sm">
        <h3 className="text-lg font-bold mb-3">Instructions</h3>
        <ol className="text-sm text-gray-300 space-y-3">
          {[
            'Download the agent archive and extract to any folder',
            'Config is already included with your Discord ID — no setup needed',
            'Or generate a pairing code above and enter it on first launch instead',
            'Run discord-task-agent.exe',
            'Whisper model (~1.5 GB) will download on first launch — please wait',
            'Status "listening" in terminal — agent is ready. Join a Discord voice channel',
          ].map((step, i) => (
            <li key={i} className="flex items-start gap-3">
              <span className="flex items-center justify-center w-6 h-6 rounded-full bg-indigo-600 text-white text-xs font-bold shrink-0 mt-0.5">
                {i + 1}
              </span>
              <span className="leading-relaxed">{step}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
