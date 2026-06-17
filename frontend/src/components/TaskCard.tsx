import type { Task } from '../api';

interface TaskCardProps {
  task: Task;
  onStatusChange: (id: string, status: string) => void;
}

const urgencyColors: Record<string, string> = {
  high: 'border-red-500',
  medium: 'border-yellow-500',
  low: 'border-green-500',
};

const urgencyLabels: Record<string, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
};

const statusLabels: Record<string, string> = {
  open: 'Open',
  in_progress: 'In Progress',
  done: 'Done',
};

export default function TaskCard({ task, onStatusChange }: TaskCardProps) {
  const borderColor = urgencyColors[task.urgency] || 'border-gray-500';
  const nextStatus = task.status === 'open' ? 'in_progress' : task.status === 'in_progress' ? 'done' : 'open';

  return (
    <div className={`border-l-4 ${borderColor} bg-gray-800 rounded-lg p-4 mb-3 shadow-sm`}>
      <div className="flex justify-between items-start gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-xs text-gray-400 mb-1">
            {task.source && <span className="bg-gray-700 px-1.5 py-0.5 rounded">[{task.source}]</span>}
            {task.author && <span>{task.author}</span>}
            {task.channel && <span>#{task.channel}</span>}
            {task.urgency && (
              <span className={`ml-auto text-xs font-medium ${
                task.urgency === 'high' ? 'text-red-400' :
                task.urgency === 'medium' ? 'text-yellow-400' :
                'text-green-400'
              }`}>
                {urgencyLabels[task.urgency]}
              </span>
            )}
          </div>
          <p className="text-gray-100 leading-relaxed">{task.content}</p>
          {task.keywords && task.keywords.length > 0 && (
            <div className="flex gap-1 mt-2 flex-wrap">
              {task.keywords.map((kw, i) => (
                <span key={i} className="text-xs bg-gray-700 text-gray-300 px-2 py-0.5 rounded">{kw}</span>
              ))}
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${
            task.status === 'done' ? 'bg-green-700 text-green-200' :
            task.status === 'in_progress' ? 'bg-blue-700 text-blue-200' :
            'bg-gray-600 text-gray-200'
          }`}>
            {statusLabels[task.status] || task.status}
          </span>
          {task.status !== 'done' && (
            <button
              onClick={() => onStatusChange(task.id, nextStatus)}
              className={`px-3 py-1.5 rounded text-xs font-medium transition ${
                nextStatus === 'in_progress'
                  ? 'bg-indigo-600 hover:bg-indigo-500 text-white'
                  : 'bg-green-600 hover:bg-green-500 text-white'
              }`}
            >
              {nextStatus === 'in_progress' ? 'Mark in progress' : 'Mark done'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
