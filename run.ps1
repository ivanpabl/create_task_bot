param(
    [ValidateSet('docker', 'local')]
    [string]$Mode = 'local'
)

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ($Mode -eq 'docker') {
    Write-Host "=== Docker Compose ===" -ForegroundColor Cyan
    docker compose up --build
    exit
}

# ── Local dev mode ─────────────────────────────────────────────────────
Write-Host "=== Local Dev: PostgreSQL + Backend + Bot + Frontend ===" -ForegroundColor Cyan

# Check prerequisites
$Missing = @()
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { $Missing += 'python' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { $Missing += 'node' }
if ($Missing) { Write-Host "Missing: $($Missing -join ', ')" -ForegroundColor Red; exit 1 }

# Ensure PostgreSQL is accessible
$pgRunning = $false
try {
    $conn = [System.Net.Sockets.TcpClient]::new()
    $conn.ConnectAsync('127.0.0.1', 5432).Wait(2000) | Out-Null
    if ($conn.Connected) { $pgRunning = $true; $conn.Close() }
} catch {}

if (-not $pgRunning) {
    Write-Host "PostgreSQL not running on localhost:5432" -ForegroundColor Yellow
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Write-Host "Starting PostgreSQL via Docker..." -ForegroundColor Yellow
        $existing = docker ps -a --filter "name=discord_tasks_pg" --format "{{.Names}}" 2>$null
        if ($existing) {
            docker start discord_tasks_pg 2>$null | Out-Null
        } else {
            docker run -d --name discord_tasks_pg `
                -e POSTGRES_USER=postgres `
                -e POSTGRES_PASSWORD=postgres `
                -e POSTGRES_DB=discord_tasks `
                -p 5432:5432 `
                postgres:16-alpine 2>$null | Out-Null
        }
        Write-Host "Waiting for PostgreSQL..." -ForegroundColor Yellow
        $ready = $false
        for ($i = 0; $i -lt 15; $i++) {
            try {
                $c = [System.Net.Sockets.TcpClient]::new()
                $c.ConnectAsync('127.0.0.1', 5432).Wait(1000) | Out-Null
                if ($c.Connected) { $ready = $true; $c.Close(); break }
            } catch {}
            Start-Sleep -Seconds 2
        }
        if (-not $ready) { Write-Host "PostgreSQL failed to start" -ForegroundColor Red; exit 1 }
    } else {
        Write-Host "Install Docker or start PostgreSQL manually on port 5432" -ForegroundColor Red
        exit 1
    }
}

$env:POSTGRES_DSN = "postgresql+asyncpg://postgres:postgres@localhost:5432/discord_tasks"
$env:BACKEND_URL = "http://localhost:8000"

# Install Python deps
Write-Host "Installing Python deps..." -ForegroundColor Green
python -m pip install -r requirements.txt -q 2>$null
python -m pip install -r desktop_agent/requirements.txt -q 2>$null

# Install frontend deps
if (-not (Test-Path "frontend/node_modules")) {
    Write-Host "Installing frontend deps..." -ForegroundColor Green
    Push-Location frontend
    npm install --silent 2>$null
    Pop-Location
}

$Jobs = @()

# Start backend
Write-Host "Starting Backend (uvicorn)..." -ForegroundColor Green
$Jobs += Start-Job -Name "backend" -ScriptBlock {
    param($Root)
    Set-Location $Root
    $env:POSTGRES_DSN = "postgresql+asyncpg://postgres:postgres@localhost:5432/discord_tasks"
    $env:BACKEND_URL = "http://localhost:8000"
    python -m uvicorn services.web.main:app --host 0.0.0.0 --port 8000
} -ArgumentList $Root

Start-Sleep -Seconds 3

# Start bot
Write-Host "Starting Bot..." -ForegroundColor Green
$Jobs += Start-Job -Name "bot" -ScriptBlock {
    param($Root)
    Set-Location $Root
    $env:BACKEND_URL = "http://localhost:8000"
    $env:POSTGRES_DSN = "postgresql+asyncpg://postgres:postgres@localhost:5432/discord_tasks"
    python run.py
} -ArgumentList $Root

# Start frontend
Write-Host "Starting Frontend (Vite)..." -ForegroundColor Green
$Jobs += Start-Job -Name "frontend" -ScriptBlock {
    param($Root)
    Set-Location "$Root/frontend"
    npm run dev
} -ArgumentList $Root

# Start desktop agent
Write-Host "Starting Desktop Agent..." -ForegroundColor Green
$Jobs += Start-Job -Name "agent" -ScriptBlock {
    param($Root, $BackendUrl)
    Set-Location $Root
    $env:BACKEND_URL = $BackendUrl
    python desktop_agent/agent.py
} -ArgumentList $Root, $env:BACKEND_URL

Write-Host ""
Write-Host "All services started!" -ForegroundColor Cyan
Write-Host "  Backend:  http://localhost:8000" -ForegroundColor White
Write-Host "  Frontend: http://localhost:5173" -ForegroundColor White
Write-Host "  Health:   http://localhost:8000/health" -ForegroundColor White
Write-Host ""
Write-Host "Press Ctrl+C to stop all services" -ForegroundColor Yellow

try {
    while ($true) {
        foreach ($job in $Jobs) {
            $msg = Receive-Job -Job $job 2>&1
            if ($msg) { Write-Host $msg }
            if ($job.State -eq 'Failed') {
                Write-Host "Job $($job.Name) FAILED!" -ForegroundColor Red
                $errorMsg = Receive-Job -Job $job 2>&1
                if ($errorMsg) { Write-Host $errorMsg -ForegroundColor Red }
                $failed = $true
            }
        }
        if ($failed) { break }
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host "Stopping all services..." -ForegroundColor Yellow
    $Jobs | Stop-Job -PassThru | Remove-Job

    # stop PG container if we started it
    $startedByUs = docker ps --filter "name=discord_tasks_pg" --format "{{.Names}}" 2>$null
    if ($startedByUs) {
        Write-Host "Stopping PostgreSQL container..." -ForegroundColor Yellow
        docker stop discord_tasks_pg 2>$null | Out-Null
    }
    Write-Host "Done." -ForegroundColor Green
}
