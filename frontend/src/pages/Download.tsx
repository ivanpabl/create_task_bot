import { useEffect, useState } from 'react';
import { downloadAgent, getMe, getPairingCode } from '../api';
import CopyButton from '../components/CopyButton';
import Icon from '../components/Icon';

const STEPS = [
  { title: 'Скачайте и распакуйте архив', text: 'В любую папку — установка не требуется.' },
  { title: 'Запустите discord-task-agent.exe', text: 'Конфиг с вашим Discord ID уже внутри архива.' },
  { title: 'Дождитесь загрузки модели', text: 'При первом запуске скачивается Whisper (~1.5 ГБ), это 1–3 минуты.' },
  { title: 'Зайдите в голосовой канал', text: 'Когда в терминале появится «listening», агент готов к работе.' },
];

function formatLeft(sec: number) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export default function Download() {
  const [discordId, setDiscordId] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');

  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [pairingLoading, setPairingLoading] = useState(false);
  const [pairingError, setPairingError] = useState('');

  useEffect(() => {
    getMe().then(u => setDiscordId(u.discord_id)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!pairingCode) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [pairingCode]);

  const secondsLeft = Math.max(0, Math.round((expiresAt - now) / 1000));
  const expired = !!pairingCode && secondsLeft === 0;

  const handleDownload = async () => {
    setDownloading(true);
    setDownloadError('');
    try {
      await downloadAgent();
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : 'Не удалось скачать агента');
    } finally {
      setDownloading(false);
    }
  };

  const generatePairingCode = async () => {
    setPairingLoading(true);
    setPairingError('');
    try {
      const data = await getPairingCode();
      setPairingCode(data.code);
      setNow(Date.now());
      setExpiresAt(Date.now() + data.expires_in * 1000);
    } catch (e) {
      setPairingError(e instanceof Error ? e.message : 'Не удалось получить код');
    } finally {
      setPairingLoading(false);
    }
  };

  const chars = pairingCode ? pairingCode.split('') : [];
  const half = Math.ceil(chars.length / 2);

  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 8 }}>
        <div>
          <h1 className="page-title">Desktop-агент</h1>
          <p className="page-sub">Записывает звук звонков Discord, распознаёт речь и создаёт задачи.</p>
        </div>
      </div>

      <section className="card card-body">
        <div className="download-hero">
          <div style={{ flex: 1, minWidth: 260 }}>
            <div className="card-title">
              <span className="feature-icon"><Icon name="windows" /></span>
              Агент для Windows
            </div>
            <div className="download-meta">
              <span><Icon name="check" />Discord ID уже в конфиге</span>
              <span><Icon name="check" />Без установки</span>
              <span><Icon name="check" />~30 МБ + модель Whisper ~1.5 ГБ</span>
            </div>
          </div>
          <button className="btn btn-primary btn-lg" onClick={handleDownload} disabled={downloading}>
            {downloading ? <span className="spinner" /> : <Icon name="download" />}
            {downloading ? 'Подготовка архива…' : 'Скачать агента'}
          </button>
        </div>

        {downloadError && <div className="alert" style={{ marginTop: 16 }}><Icon name="alert" />{downloadError}</div>}

        <div style={{ marginTop: 24 }}>
          <span className="field-label">Ваш Discord ID</span>
          <div className="copy-field">
            <code>{discordId || 'Загрузка…'}</code>
            <CopyButton value={discordId} />
          </div>
        </div>
      </section>

      <div className="grid grid-2">
        <section className="card">
          <div className="card-head">
            <div>
              <div className="card-title"><Icon name="key" />Код привязки</div>
              <p className="card-desc">Альтернатива конфигу: агент спросит код при первом запуске.</p>
            </div>
          </div>
          <div className="card-body">
            {pairingCode && !expired ? (
              <>
                <div className="pair-code" aria-label={pairingCode}>
                  {chars.slice(0, half).map((c, i) => <span key={i}>{c}</span>)}
                  <span className="sep">–</span>
                  {chars.slice(half).map((c, i) => <span key={i + half}>{c}</span>)}
                </div>
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <span className="faint" style={{ fontSize: 13 }}>
                    Действует ещё <span className="timer">{formatLeft(secondsLeft)}</span>
                  </span>
                  <div className="row">
                    <button className="btn btn-ghost btn-sm" onClick={generatePairingCode} disabled={pairingLoading}>
                      <Icon name="refresh" className="icon icon-sm" />Новый
                    </button>
                    <CopyButton value={pairingCode} />
                  </div>
                </div>
              </>
            ) : (
              <div className="stack" style={{ alignItems: 'flex-start', gap: 12 }}>
                {expired && <p className="faint" style={{ fontSize: 14 }}>Срок действия кода истёк.</p>}
                <button className="btn btn-secondary" onClick={generatePairingCode} disabled={pairingLoading}>
                  {pairingLoading ? <span className="spinner" /> : <Icon name="key" className="icon icon-sm" />}
                  {pairingLoading ? 'Генерация…' : expired ? 'Получить новый код' : 'Получить код'}
                </button>
              </div>
            )}
            {pairingError && <div className="alert" style={{ marginTop: 12 }}><Icon name="alert" />{pairingError}</div>}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <div className="card-title"><Icon name="tasks" />Как начать</div>
          </div>
          <div className="card-body">
            <ol className="steps">
              {STEPS.map(s => (
                <li key={s.title} className="step">
                  <div>
                    <h4>{s.title}</h4>
                    <p>{s.text}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>
      </div>
    </div>
  );
}
