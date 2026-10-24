import type { Task } from '../api';
import Icon from './Icon';
import SourceIcon from './SourceIcon';
import { STATUS_META, URGENCY_META } from './taskMeta';

interface TaskCardProps {
  task: Task;
  pending?: boolean;
  onStatusChange: (id: string, status: string) => void;
}

export default function TaskCard({ task, pending, onStatusChange }: TaskCardProps) {
  const urgency = URGENCY_META[task.urgency];
  const status = STATUS_META[task.status] || { label: task.status, badge: '' };
  const isDone = task.status === 'done';

  return (
    <article className={`task u-${task.urgency}${isDone ? ' is-done' : ''}${pending ? ' is-pending' : ''}`}>
      <SourceIcon source={task.source} />

      <div className="task-main">
        <p className="task-text">{task.content}</p>

        <div className="task-meta">
          {task.author && <span><Icon name="user" />{task.author}</span>}
          {task.channel && <span><Icon name="hash" />{task.channel}</span>}
          {task.assignee && <span><Icon name="check" />{task.assignee}</span>}
          {task.deadline && <span><Icon name="calendar" />{task.deadline}</span>}
        </div>

        {task.keywords && task.keywords.length > 0 && (
          <div className="task-tags">
            {task.keywords.map((kw, i) => <span key={i} className="tag">{kw}</span>)}
          </div>
        )}
      </div>

      <div className="task-side">
        <div className="row">
          {urgency && (
            <span className={`badge ${urgency.badge}`}><span className="dot" />{urgency.label}</span>
          )}
          <span className={`badge ${status.badge}`}>{status.label}</span>
        </div>

        <div className="task-actions">
          {task.status === 'open' && (
            <button className="btn btn-secondary btn-sm" onClick={() => onStatusChange(task.id, 'in_progress')}>
              <Icon name="play" className="icon icon-sm" />В работу
            </button>
          )}
          {!isDone && (
            <button className="btn btn-success btn-sm" onClick={() => onStatusChange(task.id, 'done')}>
              <Icon name="check" className="icon icon-sm" />Готово
            </button>
          )}
          {isDone && (
            <button className="btn btn-ghost btn-sm" onClick={() => onStatusChange(task.id, 'open')}>
              <Icon name="refresh" className="icon icon-sm" />Вернуть
            </button>
          )}
        </div>
      </div>
    </article>
  );
}
