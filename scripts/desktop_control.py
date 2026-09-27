#!/usr/bin/env python3
"""Bounded menu-bar bridge; delegates all research permissions to the existing CLI."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

# 在 shim 自己的命名空间执行包内实现；包内默认 ROOT=cwd（工作区），这里恢复 ROOT=仓库根 语义。
_ran_as_script = __name__ == '__main__'
__name__ = 'desktop_control'
_source = ROOT / 'src/wq/desktop_control.py'
exec(compile(_source.read_text(encoding='utf-8'), str(_source), 'exec'), globals())
ROOT = Path(__file__).resolve().parents[1]

if _ran_as_script:
    try:
        print(json.dumps(control(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None), ensure_ascii=False))
    except Exception as exc:
        # 子进程错误按界面语言在显示层翻译（菜单栏/托盘弹窗直接展示该文本）。
        try:
            _lang = ui_language(_cfg())
        except Exception:
            _lang = 'zh'
        print(json.dumps({'error': translate(str(exc), _lang)}, ensure_ascii=False))
        sys.exit(1)
