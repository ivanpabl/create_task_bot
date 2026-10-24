import { Navigate } from 'react-router-dom';
import { getAuthUrl, isAuthenticated } from '../api';
import Icon, { DiscordLogo, type IconName } from '../components/Icon';

const FEATURES: { icon: IconName; title: string; text: string }[] = [
  { icon: 'message', title: 'Из сообщений', text: 'Бот находит задачи в чатах сервера и определяет срочность.' },
  { icon: 'mic', title: 'Из голосовых звонков', text: 'Desktop-агент распознаёт речь, а LLM выделяет договорённости.' },
  { icon: 'mail', title: 'Прямо в ЛС', text: 'Найденные задачи приходят вам в личные сообщения Discord.' },
];

export default function Login() {
  if (isAuthenticated()) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="page login">
      <section>
        <span className="badge badge-accent eyebrow"><Icon name="sparkle" className="icon icon-sm" />Discord Task Bot</span>
        <h1 className="hero-title">
          Ни одна задача из разговора <span className="grad">не потеряется</span>
        </h1>
        <p className="hero-sub">
          TaskBot слушает сообщения и звонки в Discord, вытаскивает из них задачи и собирает их в одном месте.
        </p>
        <div className="features">
          {FEATURES.map(f => (
            <div key={f.title} className="feature">
              <span className="feature-icon"><Icon name={f.icon} /></span>
              <div>
                <h4>{f.title}</h4>
                <p>{f.text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card login-card">
        <span className="brand-mark"><Icon name="logo" /></span>
        <h2>Вход в TaskBot</h2>
        <p className="card-desc">Войдите через Discord, чтобы видеть свои задачи и подключить агента.</p>
        <a href={getAuthUrl()} className="btn btn-lg btn-discord">
          <DiscordLogo className="icon icon-lg" />
          Войти через Discord
        </a>
        <p className="fine">Мы запрашиваем только ваш ID и имя пользователя.</p>
      </section>
    </div>
  );
}
