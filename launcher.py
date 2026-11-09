"""Packaged entry point with startup diagnostics for no-console builds."""
import os
import sys
import traceback
from pathlib import Path

if __name__ == '__main__':
    try:
        from app import main
        sys.exit(main())
    except Exception:
        folder = Path(os.environ.get('KEY4_SMOKE_DIR') or os.environ.get('LOCALAPPDATA', '.')) / 'Key4'
        folder.mkdir(parents=True, exist_ok=True)
        log = folder / 'startup-error.txt'
        log.write_text(traceback.format_exc(), encoding='utf-8')
        if '--smoke-test' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f'启动失败，错误记录：\n{log}', 'Key4', 16)
        sys.exit(1)
