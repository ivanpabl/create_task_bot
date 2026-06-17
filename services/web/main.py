from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Scope, Receive, Send

from services.web.config import settings
from services.web.database import init_postgres
from services.database import init_db
from services.poller import start_poller
from services.web.routers import auth, tasks, messages, transcriptions, agent, pairing
from services.alerter import send_alert


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.postgres_dsn:
        init_postgres(settings.postgres_dsn)
    await start_poller()
    await send_alert('API server started', 'info')
    yield


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
