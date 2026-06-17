import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { exchangeToken, setToken } from '../api';

export default function Callback() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [error, setError] = useState('');

  useEffect(() => {
    const code = searchParams.get('code');
    if (!code) {
      setError('No authorization code received');
      return;
    }

    (async () => {
      try {
        const resp = await exchangeToken();
        if (resp.token) {
          setToken(resp.token);
          navigate('/', { replace: true });
        } else {
          setError('Failed to exchange token');
        }
      } catch (e) {
        setError('Authentication failed');
      }
    })();
  }, [searchParams, navigate]);

  if (error) {
    return <div className="text-red-400 text-center mt-10">{error}</div>;
  }

  return <div className="text-center text-gray-400 mt-10">Authenticating...</div>;
}
