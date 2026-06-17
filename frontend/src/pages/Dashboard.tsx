import { useEffect, useState } from 'react';
import { getTasks, updateTask, type Task } from '../api';
import TaskCard from '../components/TaskCard';
import TaskFilters from '../components/TaskFilters';

export default function Dashboard() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState('');
  const [urgency, setUrgency] = useState('');

  const fetchTasks = async () => {
    setLoading(true);
    try {
      const data = await getTasks({ status: status || undefined, urgency: urgency || undefined });
      setTasks(data);
    } catch {
      setTasks([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
  }, [status, urgency]);

  const handleStatusChange = async (id: string, newStatus: string) => {
    try {
      await updateTask(id, { status: newStatus });
      fetchTasks();
    } catch (e) {
      console.error(e);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[40vh]">
        <div className="text-gray-400">Loading tasks...</div>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold">Tasks</h2>
        <span className="text-sm text-gray-400">{tasks.length} task{tasks.length !== 1 ? 's' : ''}</span>
      </div>
      <TaskFilters
        status={status}
        urgency={urgency}
        onStatusChange={setStatus}
        onUrgencyChange={setUrgency}
      />
      {tasks.length === 0 ? (
        <div className="bg-gray-800 rounded-xl p-10 text-center border border-gray-700">
          <p className="text-gray-500">No tasks found.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {tasks.map(task => (
            <TaskCard key={task.id} task={task} onStatusChange={handleStatusChange} />
          ))}
        </div>
      )}
    </div>
  );
}
