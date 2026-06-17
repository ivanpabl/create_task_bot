import { getAuthUrl, isAuthenticated } from '../api';
import { Navigate } from 'react-router-dom';

export default function Login() {
  if (isAuthenticated()) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="flex items-center justify-center min-h-[70vh]">
      <div className="bg-gray-800 rounded-xl p-8 text-center max-w-sm shadow-lg border border-gray-700">
        <div className="text-4xl mb-4">🤖</div>
        <h1 className="text-xl font-bold mb-1">TaskBot Dashboard</h1>
        <p className="text-gray-400 text-sm mb-6">
          Sign in with Discord to manage your tasks
        </p>
        <a
          href={getAuthUrl()}
          className="inline-block bg-indigo-600 hover:bg-indigo-500 text-white font-semibold px-8 py-3 rounded-lg transition shadow-md hover:shadow-lg"
        >
          Sign in with Discord
        </a>
      </div>
    </div>
  );
}
