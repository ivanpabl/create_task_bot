import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { exchangeToken, setToken } from '../api';
import Icon from '../components/Icon';

export default function Callback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const hasCode = !!searchParams.get('code');
  const [exchangeError, setError] = useState('');
  const error = hasCode ? exchangeError : 'Discord не вернул код авторизации';

  useEffect(() => {
    if (!hasCode) return;

    (async () => {
      try {
        const resp = await exchangeToken();
        if (resp.token) {
          setToken(resp.token);
          navigate('/', { replace: true });
        } else {
          setError('Не удалось получить токен');
        }
      } catch {
        setError('Ошибка авторизации');
      }
    })();
  }, [hasCode, navigate]);

  if (error) {
    return (
      <div className="page center">
        <div className="empty-icon" style={{ color: 'var(--red)' }}><Icon name="alert" className="icon icon-lg" /></div>
        <div>
          <h2 className="card-title" style={{ justifyContent: 'center' }}>{error}</h2>
          <p className="muted">Попробуйте войти ещё раз.</p>
        </div>
        <Link to="/login" className="btn btn-primary">Вернуться ко входу</Link>
      </div>
    );
  }

  return (
    <div className="page center">
      <span className="spinner spinner-lg" />
      <p className="muted">Выполняем вход…</p>
    </div>
  );
}
