"""
Pre-build base.zip (full zip with config.yml stripped) so the backend
can cache it instantly at startup without a 5–15 second delay on first request.
"""
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FULL_ZIP = PROJECT_ROOT / 'desktop_agent' / 'dist' / 'discord-task-agent.zip'
BASE_ZIP = PROJECT_ROOT / 'desktop_agent' / 'dist' / 'base.zip'


def main():
    if not FULL_ZIP.is_file():
        raise SystemExit(f'Full zip not found: {FULL_ZIP} – build the desktop agent first.')

    with zipfile.ZipFile(FULL_ZIP, 'r') as src:
        with zipfile.ZipFile(BASE_ZIP, 'w', zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                if info.filename != 'config.yml':
                    dst.writestr(info, src.read(info.filename))

    print(f'✅ base.zip created ({BASE_ZIP.stat().st_size / 1024:.1f} KB)')


if __name__ == '__main__':
    main()
