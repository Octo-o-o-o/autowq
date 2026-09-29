"""本机官方 CLI 入口；不读取或复制登录凭证。由外层 sandbox-exec 启动。

调用形态：provider_entry.py <provider> <workdir> <prompt_file> <model>
doctor 探活：provider_entry.py <provider> --version   （转发底层 CLI 的 --version）
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ZCODE_CJS = '/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs'
ZCODE_PROVIDER_CONFIG = '/Applications/ZCode.app/Contents/Resources/config/provider/zcode-builtin.json'
# 已按 0.16.9 的工具名单核验：再派单、持久化调度、跨会话读取与浏览器/联网面全部关闭。
# mcp__* 工具名随插件安装而异，未列于此；沙箱与 prompt 契约仍是第二、三层约束。
ZCODE_DISALLOWED_TOOLS = (
    'Agent,Task,BrowserUse,WebSearch,WebFetch,AskUserQuestion,'
    'CronCreate,CronUpdate,CronDelete,CronList,OffPeakCreate,OffPeakList,'
    'SendMessage,ReadSessionContext,Skill,CreateWorkflow,AmendWorkflow,ResumeWorkflowRun')
# prompt 以单个 argv 元素传给底层 CLI（无 --prompt-file 可用）；超限会在 execve 阶段
# 以 E2BIG 崩溃，这里提前给出可读错误。
PROMPT_LIMIT = 200_000


def node_binary():
    binary = os.environ.get('WQ_NODE_BIN') or shutil.which('node')
    if not binary:
        raise SystemExit('node not found; set WQ_NODE_BIN / 未找到 node，可设 WQ_NODE_BIN')
    return binary


def zcode_version(binary):
    """本地查询 CLI 版本；失败返回 None，不触发模型调用。"""
    try:
        r = subprocess.run([binary, ZCODE_CJS, '--version'], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or r.stderr).strip()
    return out.split()[0] if out else None


def build_argv(provider, workdir, prompt_file, model):
    """纯构造命令行，便于单测；返回 (binary, argv)，调用方负责 exec。"""
    prompt = Path(prompt_file).read_text()
    if len(prompt) > PROMPT_LIMIT:
        raise SystemExit(f'prompt exceeds {PROMPT_LIMIT} chars; trim request.md / '
                         f'prompt 超过 {PROMPT_LIMIT} 字符，请缩减 request.md')
    if provider == 'cursor':
        binary = os.environ.get('WQ_CURSOR_BIN') or str(Path.home()/'.local/bin/cursor-agent')
        argv = [binary, '--workspace', workdir, '--model', model, '--print', '--force',
                '--trust', '--output-format', 'json', prompt]
    elif provider == 'zcode':
        for path in (ZCODE_CJS, ZCODE_PROVIDER_CONFIG):
            if not Path(path).is_file():
                raise SystemExit('ZCode app file missing: ' + path + ' / ZCode 应用文件缺失，请重装或更新 launchers')
        verified = os.environ.get('WQ_ZCODE_VERIFIED_VERSION', '')
        if verified:
            current = zcode_version(node_binary())
            if current != verified:
                raise SystemExit(f'ZCode CLI {current or "unknown"} != verified {verified}; '
                                 're-verify one real call / CLI 版本与已核验版本不同，需重新单轮核验')
        os.environ['ZCODE_BUILTIN_PROVIDER_CONFIG_FILE'] = ZCODE_PROVIDER_CONFIG
        binary = node_binary()
        argv = [binary, ZCODE_CJS,
                '--cwd', workdir, '--mode', 'yolo', '--json', '--prompt', prompt,
                '--disallowed-tools', ZCODE_DISALLOWED_TOOLS]
    else:
        raise SystemExit('unknown provider')
    return binary, argv


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[1] in ('--version', '-v'):
        # doctor --probe 通过 launcher 探活：转发底层 CLI 的版本查询，不读 prompt、不发调用。
        if args[0] == 'cursor':
            binary = os.environ.get('WQ_CURSOR_BIN') or str(Path.home()/'.local/bin/cursor-agent')
            os.execv(binary, [binary, '--version'])
        elif args[0] == 'zcode':
            binary = node_binary()
            os.execv(binary, [binary, ZCODE_CJS, '--version'])
        else:
            raise SystemExit('unknown provider')
        return
    provider, workdir, prompt_file, model = args
    binary, argv = build_argv(provider, workdir, prompt_file, model)
    os.execv(binary, argv)


if __name__ == '__main__':
    main()
