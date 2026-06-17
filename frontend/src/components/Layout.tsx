import { Outlet, Link, useNavigate, useLocation } from 'react-router-dom';
import { isAuthenticated, clearToken } from '../api';

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const authed = isAuthenticated();

  const handleLogout = () => {
    clearToken();
    navigate('/login');
  };

  const isActive = (path: string) => location.pathname === path;

  const linkClass = (path: string) =>
    `px-4 py-2 rounded-lg text-sm font-medium border transition ${
      isActive(path)
        ? 'bg-indigo-600 border-indigo-500 text-white'
        : 'border-gray-600 text-gray-300 hover:bg-gray-700 hover:border-gray-500 hover:text-white'
    }`;

  return (
    <div className="min-h-screen bg-gray-900 text-gray-100">
      <nav className="bg-gray-800 border-b border-gray-700 px-6 py-2 flex items-center justify-between shadow-sm">
        <div className="flex items-center gap-2">
          <Link to="/" className="text-lg font-bold text-indigo-400 mr-4">TaskBot</Link>
          {authed && (
            <div className="flex gap-1">
              <Link to="/" className={linkClass('/')}>Tasks</Link>
              <Link to="/download" className={linkClass('/download')}>Download Agent</Link>
              <Link to="/settings" className={linkClass('/settings')}>Settings</Link>
            </div>
          )}
        </div>
        {authed && (
          <button
            onClick={handleLogout}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-red-700 text-red-400 hover:bg-red-600 hover:border-red-500 hover:text-white transition"
          >
            Logout
          </button>
        )}
      </nav>
      <main className="p-6 max-w-5xl mx-auto">
        <Outlet />
      </main>
    </div>
  );
}
