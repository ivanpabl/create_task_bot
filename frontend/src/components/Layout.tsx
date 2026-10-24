import { useEffect, useState } from 'react';
import { Outlet, Link, NavLink, useNavigate } from 'react-router-dom';
import { isAuthenticated, clearToken, getMe, type User } from '../api';
import Icon, { type IconName } from './Icon';

const NAV: { to: string; label: string; icon: IconName }[] = [
  { to: '/', label: 'Задачи', icon: 'tasks' },
  { to: '/download', label: 'Скачать агента', icon: 'download' },
  { to: '/settings', label: 'Агент', icon: 'agent' },
];

export default function Layout() {
  const navigate = useNavigate();
  const authed = isAuthenticated();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    if (!authed) return;
    getMe().then(setUser).catch(() => {});
  }, [authed]);

  const handleLogout = () => {
    clearToken();
    setUser(null);
    navigate('/login');
  };

  return (
    <>
      <header className="header">
        <div className="header-inner">
          <Link to="/" className="brand">
            <span className="brand-mark"><Icon name="logo" /></span>
            <span className="brand-name">TaskBot</span>
          </Link>

          {authed && (
            <nav className="nav">
              {NAV.map(item => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end
                  className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
                >
                  <Icon name={item.icon} />
                  <span className="label">{item.label}</span>
                </NavLink>
              ))}
            </nav>
          )}

          {authed && (
            <div className="header-user">
              {user && (
                <>
                  <span className="avatar" aria-hidden="true">{user.username.slice(0, 1).toUpperCase()}</span>
                  <span className="user-name">{user.username}</span>
                </>
              )}
              <button className="btn btn-ghost btn-icon" onClick={handleLogout} title="Выйти" aria-label="Выйти">
                <Icon name="logout" />
              </button>
            </div>
          )}
        </div>
      </header>
      <main className="main">
        <Outlet />
      </main>
    </>
  );
}
