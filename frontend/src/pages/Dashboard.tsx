import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { getTasks, updateTask, type Task } from '../api';
import Icon, { type IconName } from '../components/Icon';
import TaskCard from '../components/TaskCard';
import TaskFilters from '../components/TaskFilters';

function Stat({ icon, label, value, hint, color, children }: {
  icon: IconName; label: string; value: number; hint?: string; color?: string; children?: React.ReactNode;
}) {
  return (
    <div className="stat">
      <div className="stat-label" style={color ? { color } : undefined}><Icon name={icon} />{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
      {children}
    </div>
  );
}

export default function Dashboard() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [status, setStatus] = useState('');
  const [urgency, setUrgency] = useState('');
  const [query, setQuery] = useState('');

  const [reloadKey, setReloadKey] = useState(0);

  // Filters are applied client-side: one request, instant switching, and counters stay accurate.
  useEffect(() => {
    let cancelled = false;
    getTasks()
      .then(data => { if (!cancelled) { setTasks(data); setError(''); } })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Не удалось загрузить задачи'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  const reload = () => {
    setLoading(true);
    setReloadKey(k => k + 1);
  };

  const handleStatusChange = async (id: string, newStatus: string) => {
    const prev = tasks;
    setTasks(ts => ts.map(t => (t.id === id ? { ...t, status: newStatus } : t)));
    setPending(p => new Set(p).add(id));
    try {
      const updated = await updateTask(id, { status: newStatus });
      setTasks(ts => ts.map(t => (t.id === id ? updated : t)));
    } catch (e) {
      setTasks(prev);
      setError(e instanceof Error ? e.message : 'Не удалось обновить задачу');
    } finally {
      setPending(p => { const n = new Set(p); n.delete(id); return n; });
    }
  };

  const byUrgency = useMemo(
    () => (urgency ? tasks.filter(t => t.urgency === urgency) : tasks),
    [tasks, urgency],
  );

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: byUrgency.length, open: 0, in_progress: 0, done: 0 };
    for (const t of byUrgency) c[t.status] = (c[t.status] || 0) + 1;
    return c;
  }, [byUrgency]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return byUrgency.filter(t => {
      if (status && t.status !== status) return false;
      if (!q) return true;
      return [t.content, t.author, t.channel, t.assignee, ...(t.keywords || [])]
        .some(v => v?.toLowerCase().includes(q));
    });
  }, [byUrgency, status, query]);

  const total = tasks.length;
  const done = tasks.filter(t => t.status === 'done').length;
  const inProgress = tasks.filter(t => t.status === 'in_progress').length;
  const urgent = tasks.filter(t => t.urgency === 'high' && t.status !== 'done').length;
  const donePct = total ? Math.round((done / total) * 100) : 0;
  const filtered = !!(status || urgency || query);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Задачи</h1>
          <p className="page-sub">Собраны из сообщений и голосовых звонков в Discord</p>
        </div>
        <button className="btn btn-secondary" onClick={reload} disabled={loading}>
          <Icon name="refresh" className="icon icon-sm" />Обновить
        </button>
      </div>

      <div className="stats">
        <Stat icon="inbox" label="Всего" value={total} hint="задач найдено" />
        <Stat icon="clock" label="В работе" value={inProgress} hint="сейчас выполняются" color="var(--blue)" />
        <Stat icon="flame" label="Срочные" value={urgent} hint="высокий приоритет" color="var(--red)" />
        <Stat icon="check" label="Выполнено" value={done} hint={`${donePct}% от всех`} color="var(--green)">
          <div className="progress"><span style={{ width: `${donePct}%` }} /></div>
        </Stat>
      </div>

      <TaskFilters
        status={status}
        urgency={urgency}
        query={query}
        counts={counts}
        onStatusChange={setStatus}
        onUrgencyChange={setUrgency}
        onQueryChange={setQuery}
      />

      {error && (
        <div className="alert" style={{ marginBottom: 16 }}>
          <Icon name="alert" />{error}
        </div>
      )}

      {loading ? (
        <div className="task-list">
          {[0, 1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 92 }} />)}
        </div>
      ) : visible.length === 0 ? (
        <div className="empty">
          <div className="empty-icon"><Icon name={filtered ? 'search' : 'inbox'} className="icon icon-lg" /></div>
          {filtered ? (
            <>
              <h3>Ничего не найдено</h3>
              <p>Попробуйте изменить фильтры или поисковый запрос.</p>
              <button
                className="btn btn-secondary btn-sm"
                style={{ marginTop: 16 }}
                onClick={() => { setStatus(''); setUrgency(''); setQuery(''); }}
              >
                Сбросить фильтры
              </button>
            </>
          ) : (
            <>
              <h3>Задач пока нет</h3>
              <p>Они появятся, когда бот найдёт их в сообщениях или агент — в голосовых звонках.</p>
              <Link to="/download" className="btn btn-primary btn-sm" style={{ marginTop: 16 }}>
                <Icon name="download" className="icon icon-sm" />Установить агента
              </Link>
            </>
          )}
        </div>
      ) : (
        <div className="task-list">
          {visible.map(task => (
            <TaskCard
              key={task.id}
              task={task}
              pending={pending.has(task.id)}
              onStatusChange={handleStatusChange}
            />
          ))}
        </div>
      )}
    </div>
  );
}
