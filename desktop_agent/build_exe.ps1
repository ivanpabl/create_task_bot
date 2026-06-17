param(
    [string]$OutputDir = "$PSScriptRoot\dist\discord-task-agent"
)

$ErrorActionPreference = "Stop"

# Check Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found" -ForegroundColor Red; exit 1
}

# Install PyInstaller if missing
try { python -m PyInstaller --version 2>$null } catch {
    Write-Host "Installing PyInstaller..." -ForegroundColor Yellow
    python -m pip install pyinstaller -q
}

# Ensure compatible numpy
$numpyVer = python -c "import numpy; print(numpy.__version__)" 2>$null
if ($numpyVer -and $numpyVer -ge "2") {
    Write-Host "Downgrading numpy for compatibility..." -ForegroundColor Yellow
    python -m pip install "numpy<2" -q
}

# Resolve faster_whisper assets path (includes silero_vad_v6.onnx for VAD filter)
$fasterWhisperAssets = python -c "import faster_whisper, os; print(os.path.join(os.path.dirname(faster_whisper.__file__), 'assets'))" 2>$null
if (-not $fasterWhisperAssets -or -not (Test-Path $fasterWhisperAssets)) {
    Write-Host "Failed to find faster_whisper assets directory!" -ForegroundColor Red
    exit 1
}

# Resolve silero_vad data path (model files: .jit, .onnx, .safetensors)
$sileroVadData = python -c "import silero_vad.data, os; print(os.path.dirname(silero_vad.data.__file__))" 2>$null
if (-not $sileroVadData -or -not (Test-Path $sileroVadData)) {
    Write-Host "Failed to find silero_vad data directory!" -ForegroundColor Red
    exit 1
}

Write-Host "=== Building Desktop Agent executable ===" -ForegroundColor Cyan

# Clean previous build
foreach ($item in @("$PSScriptRoot\build", "$PSScriptRoot\dist")) {
    if (Test-Path $item) { Remove-Item -Recurse -Force $item }
}
Remove-Item -Force "$PSScriptRoot\*.spec" -ErrorAction SilentlyContinue

python -m PyInstaller --onedir `
    --name "discord-task-agent" `
    --distpath "$PSScriptRoot\dist" `
    --workpath "$PSScriptRoot\build" `
    --specpath "$PSScriptRoot" `
    --add-data "$PSScriptRoot\config.yml.example;." `
    --hidden-import "discord_rpc" `
    --hidden-import "wasapi_capture" `
    --hidden-import "faster_whisper" `
    --hidden-import "ctranslate2" `
    --hidden-import "silero_vad" `
    --hidden-import "silero_vad.data" `
    --hidden-import "silero_vad.utils_vad" `
    --hidden-import "silero_vad.model" `
    --add-data "$fasterWhisperAssets;faster_whisper/assets" `
    --add-data "$sileroVadData;silero_vad/data" `
    --hidden-import "sounddevice" `
    --hidden-import "sounddevice._portaudio" `
    --hidden-import "win32file" `
    --hidden-import "win32pipe" `
    --hidden-import "pywintypes" `
    --hidden-import "websockets" `
    --hidden-import "yaml" `
    --hidden-import "dotenv" `
    --hidden-import "httpx" `
    --hidden-import "torchaudio" `
    --exclude-module "matplotlib" `
    --exclude-module "tkinter" `
    --exclude-module "torchvision" `
    --exclude-module "transformers" `
    --exclude-module "datasets" `
    --exclude-module "tensorflow" `
    --exclude-module "cv2" `
    --exclude-module "IPython" `
    --exclude-module "jedi" `
    --exclude-module "pytest" `
    --exclude-module "PIL" `
    --exclude-module "pandas" `
    --exclude-module "scipy" `
    --exclude-module "sklearn" `
    --exclude-module "lxml" `
    "$PSScriptRoot\agent.py"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build FAILED!" -ForegroundColor Red
    exit 1
}

# Read backend_url from source config.yml
$sourceConfig = Get-Content "$PSScriptRoot\config.yml" -Raw
if ($sourceConfig -match "backend_url:\s*['""]?(https?://[^\s""']+)['""]?\s*") {
    $backendUrl = $matches[1]
} else {
    $backendUrl = "http://localhost:8000"
}

# Create config.yml with real backend_url, empty discord_id
$configContent = @"
# Discord Task Bot — Desktop Agent Configuration
# Получите ваш Discord ID на странице /download после авторизации

# Вставьте сюда ваш Discord ID
discord_id: ""

# Адрес сервера (не меняйте)
backend_url: "$backendUrl"

# Настройки Whisper (опционально)
whisper_model: medium
whisper_device: cpu
whisper_compute: int8
"@
[System.IO.File]::WriteAllText("$OutputDir\config.yml", $configContent, [System.Text.UTF8Encoding]::new($false))

# Copy config template as well
Copy-Item "$PSScriptRoot\config.yml.example" "$OutputDir\config.yml.example" -Force

# Copy additional Python modules next to exe (for dynamic imports)
# Copy additional Python modules next to exe (for dynamic imports)
if (Test-Path "$OutputDir\_internal") {
    Copy-Item "$PSScriptRoot\discord_rpc.py" "$OutputDir\_internal\" -Force
    Copy-Item "$PSScriptRoot\wasapi_capture.py" "$OutputDir\_internal\" -Force
} else {
    Copy-Item "$PSScriptRoot\discord_rpc.py" "$OutputDir\" -Force
    Copy-Item "$PSScriptRoot\wasapi_capture.py" "$OutputDir\" -Force
}



# Create README
@"
====================================================
  Discord Task Agent — готов к запуску!
====================================================

Как настроить:
1. Откройте config.yml и вставьте ваш Discord ID
   (страница /download на сайте после авторизации)
2. Укажите backend_url (адрес вашего сервера)
3. Запустите discord-task-agent.exe

При первом запуске скачается модель Whisper (~1.5 GB).
Терминал покажет статус подключения к бэкенду.

Подробнее: https://github.com/ваш-репозиторий
"@ | Out-File "$OutputDir\README.txt" -Encoding UTF8

# Create ZIP
Write-Host "Creating ZIP archive..." -ForegroundColor Yellow
$zipPath = "$PSScriptRoot\dist\discord-task-agent.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($OutputDir, $zipPath)

# Create base.zip (without config.yml) for fast dynamic serving
Write-Host "Creating base.zip (without config.yml)..." -ForegroundColor Yellow
$baseZipPath = "$PSScriptRoot\dist\base.zip"
if (Test-Path $baseZipPath) { Remove-Item $baseZipPath -Force }
python -c @"
import zipfile
src = r'$($zipPath.Replace('\', '/'))'
dst = r'$($baseZipPath.Replace('\', '/'))'
with zipfile.ZipFile(src, 'r') as zin:
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename != 'config.yml':
                zout.writestr(info, zin.read(info.filename))
"@

Write-Host ""
Write-Host "=== Build complete! ===" -ForegroundColor Green
Write-Host "Output: $OutputDir" -ForegroundColor White
Write-Host "ZIP:    $zipPath" -ForegroundColor White
Write-Host "Base:   $baseZipPath" -ForegroundColor White
Write-Host "Size:   " -NoNewline
$size = (Get-ChildItem -Path $OutputDir -Recurse | Measure-Object -Property Length -Sum).Sum
Write-Host "$([math]::Round($size / 1MB, 1)) MB" -ForegroundColor White
Write-Host ""
Write-Host "Next: загрузите ZIP на сервер в папку desktop_agent/dist/ для раздачи через /api/agent/download" -ForegroundColor Cyan
