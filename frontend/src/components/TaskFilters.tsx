import Icon from './Icon';

interface TaskFiltersProps {
  status: string;
  urgency: string;
  query: string;
  counts: Record<string, number>;
  onStatusChange: (s: string) => void;
  onUrgencyChange: (u: string) => void;
  onQueryChange: (q: string) => void;
}

const STATUS_TABS = [
  { value: '', label: 'Все' },
  { value: 'open', label: 'Открытые' },
  { value: 'in_progress', label: 'В работе' },
  { value: 'done', label: 'Готово' },
];

export default function TaskFilters({
  status, urgency, query, counts, onStatusChange, onUrgencyChange, onQueryChange,
}: TaskFiltersProps) {
  return (
    <div className="toolbar">
      <div className="segmented" role="tablist" aria-label="Статус">
        {STATUS_TABS.map(tab => (
          <button
            key={tab.value}
            role="tab"
            aria-selected={status === tab.value}
            className={status === tab.value ? 'active' : ''}
            onClick={() => onStatusChange(tab.value)}
          >
            {tab.label}
            <span className="count">{counts[tab.value || 'all'] ?? 0}</span>
          </button>
        ))}
      </div>

      <label className="search">
        <span className="sr-only">Поиск</span>
        <Icon name="search" />
        <input
          className="input"
          type="search"
          placeholder="Поиск по задачам, авторам, каналам…"
          value={query}
          onChange={e => onQueryChange(e.target.value)}
        />
      </label>

      <select
        className="select"
        style={{ width: 'auto', minWidth: 170 }}
        value={urgency}
        onChange={e => onUrgencyChange(e.target.value)}
        aria-label="Срочность"
      >
        <option value="">Любая срочность</option>
        <option value="high">Высокая</option>
        <option value="medium">Средняя</option>
        <option value="low">Низкая</option>
      </select>
    </div>
  );
}
