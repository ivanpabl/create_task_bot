import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Scope, Receive, Send

from services.web.config import settings
from services.web.database import init_postgres, get_session_factory
from services.database import init_db
from services.poller import start_poller
from services.web.routers import auth, tasks, messages, transcriptions, agent, pairing
from services.web.agent_manager import manager as agent_manager
from services.alerter import send_alert
from services.analytics.clickhouse import ClickHouseClient, run_sync_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.postgres_dsn:
        init_postgres(settings.postgres_dsn)
    await start_poller()
    agent_manager.start()
    ch_sync = _start_clickhouse_sync()
    _warn_insecure_backend_url()
    await send_alert('API server started', 'info')
    yield
    if ch_sync:
        ch_sync.cancel()
    await agent_manager.stop()


def _start_clickhouse_sync() -> asyncio.Task | None:
    factory = get_session_factory()
    if not settings.clickhouse_url or factory is None:
        return None
    ch = ClickHouseClient(settings.clickhouse_url, settings.clickhouse_user,
                          settings.clickhouse_password, settings.clickhouse_database)
    return asyncio.create_task(run_sync_loop(factory, ch))


def _warn_insecure_backend_url():
    # BACKEND_URL is baked into every downloaded agent config and decides ws:// vs wss://.
    parsed = urlparse(settings.backend_url)
    if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        print(f"[server] WARNING: BACKEND_URL={settings.backend_url} is not https. "
              f"Agents will refuse to connect; set BACKEND_URL=https://... behind a TLS proxy.")


app = FastAPI(title='Discord Task Bot API', lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret)


class UserAgentLogMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope['type'] == 'http':
            headers = dict(scope.get('headers', []))
            ua = headers.get(b'user-agent', b'').decode(errors='replace')
            ip = scope.get('client', ('?', 0))[0]
            path = scope.get('path', '?')
            if ua:
                print(f"[req] {ip} {path} | {ua[:120]}")
        await self.app(scope, receive, send)


app.add_middleware(UserAgentLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, 'https://studio-questions-disclosure-chen.trycloudflare.com'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(messages.router)
app.include_router(transcriptions.router)
app.include_router(agent.router)
app.include_router(pairing.router)


@app.get('/rpc-callback')
async def rpc_callback():
    return HTMLResponse("""
<!DOCTYPE html>
<html><body><h2>Авторизация RPC завершена</h2>
<p>Можете закрыть эту вкладку и вернуться в терминал с агентом.</p></body></html>
""")


@app.get('/health')
async def health():
    return {'status': 'ok'}


# Serve built frontend
_frontend_dir = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
if _frontend_dir.is_dir():
    app.mount('/', StaticFiles(directory=str(_frontend_dir), html=True), name='frontend')
    print(f"[server] Serving frontend from {_frontend_dir}")
else:
    print(f"[server] Frontend build not found at {_frontend_dir} (run 'cd frontend && npm run build')")
