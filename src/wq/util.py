"""小工具：时间、规范化 JSON、hash、文件读取。只依赖标准库。"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

UTC = dt.timezone.utc


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def now_iso() -> str:
    return now().isoformat(timespec="milliseconds")


def parse_iso(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d


def iso_week(d: dt.datetime | None = None) -> str:
    d = d or now()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def canonical_json(obj) -> str:
    """确定性序列化，用于去重 hash。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_json(obj) -> str:
    return sha256_text(canonical_json(obj))


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, obj) -> None:
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def ensure_dir(path: str, mode: int | None = None) -> str:
    os.makedirs(path, exist_ok=True)
    if mode is not None:
        os.chmod(path, mode)
    return path


def kill_tree(pid: int, force: bool) -> None:
    """终止一次本地调用所在的进程组：POSIX 用 killpg，Windows 用 taskkill 结束进程树。"""
    if sys.platform == 'win32':
        import subprocess
        try:
            subprocess.run(['taskkill', '/PID', str(pid), '/T'] + (['/F'] if force else []),
                           capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass   # 与 POSIX 分支一致：终止失败只记录现场，不中断调用方流程
        return
    import signal
    try:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def eprint(*args) -> None:
    print(*args, file=sys.stderr)
