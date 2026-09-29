"""Single text/JSON inference adapter; also copied into isolated CLI runtimes."""
import email.utils
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit

PROTOCOLS = ('openai', 'openai-responses', 'anthropic')


def validate_api(item):
    allowed={'kind','protocol','model','base_url','api_key_env','api_key_file','allow_no_key','max_tokens','timeout_s','token_parameter'}
    if not isinstance(item,dict) or set(item)-allowed:
        raise ValueError('API config allows key references only; no inline keys or arbitrary headers')
    if type(item.get('allow_no_key',False)) is not bool:
        raise ValueError('allow_no_key must be boolean')
    if type(item.get('timeout_s',120)) is not int or not 1<=item.get('timeout_s',120)<=900:
        raise ValueError('API timeout_s must be 1..900')
    if item.get('token_parameter','max_tokens') not in ('max_tokens','max_completion_tokens'):
        raise ValueError('Invalid token_parameter')
    if item.get('protocol') not in PROTOCOLS:
        raise ValueError('Unsupported API protocol')
    u = urlsplit(item.get('base_url', ''))
    if not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('Base URL must not contain credentials, query or fragment')
    if u.scheme == 'https':
        pass
    elif u.scheme == 'http' and _private_http_host(u.hostname):
        pass
    else:
        raise ValueError('Use HTTPS. HTTP is only allowed for loopback or a private-network address')
    if not item.get('model'):
        raise ValueError('Explicit model required')
    if type(item.get('max_tokens',4096)) is not int or not 1 <= item.get('max_tokens', 4096) <= 131072:
        raise ValueError('max_tokens must be 1..131072')


def _private_http_host(hostname):
    """本机或用户自己网络里的地址可以用 HTTP。公网地址必须 HTTPS，避免密钥明文出门。"""
    if hostname.lower() == 'localhost':
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    # link-local（169.254/16、fe80::/10）含云主机元数据地址，不是用户自己的服务，不给明文 HTTP。
    return address.is_loopback or (address.is_private and not address.is_link_local)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to a redirect destination.


def api_request(item, path, body=None):
    validate_api(item)
    key = os.environ.get(item.get('api_key_env', ''), '')
    if not key and item.get('api_key_file'):
        p = Path(item['api_key_file']).expanduser()
        if p.stat().st_mode & 0o077:
            raise ValueError('API key file requires mode 0600')
        key = p.read_text().strip()
    if not key and not item.get('allow_no_key', False):
        raise ValueError('API key missing; configure environment or private key file')
    headers = {'Content-Type': 'application/json'}
    if item['protocol'] == 'anthropic':
        headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
    elif key:
        headers['Authorization'] = 'Bearer ' + key
    request = urllib.request.Request(item['base_url'].rstrip('/') + path,
        data=None if body is None else json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=item.get('timeout_s', 120)) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # Bodies/URLs can echo keys; never log them. Only a fixed classification token leaves this function.
        try:
            body = e.read(8192).decode('utf-8', errors='replace')
        except Exception:
            body = ''
        raise ValueError('API HTTP ' + str(e.code) + limit_suffix(e.code, body, e.headers)) from None
    except urllib.error.URLError:
        raise ValueError('API transport failed; check endpoint/network') from None


# 额度耗尽（日/月/赠额/余额）与短时限流分开：前者暂停该渠道到重置时刻，后者按 Retry-After 退避。
# Gemini 两类都返回 429 RESOURCE_EXHAUSTED，靠 quotaId 的 PerDay/PerMinute 区分。
_QUOTA_RE = re.compile(r'(?i)(insufficient[_ ]?quota|exceeded your current quota|quota[^\n]{0,40}(exceed|exhaust)'
                       r'|per[ -]?day|requests? per day|\brpd\b|daily (limit|quota)|free[ _-]?tier[^\n]{0,30}(limit|exhaust|only)'
                       r'|allocationquota|insufficient[^\n]{0,20}(credit|balance|fund)|credits?[^\n]{0,20}(exhaust|insufficient|run out)'
                       r'|(weekly|monthly|usage|plan)[^\n]{0,20}limit[^\n]{0,10}(exhaust|reach)|arrearage|余额不足|欠费'
                       r'|额度.{0,12}(不足|用尽|用完|耗尽|超)|免费.{0,8}(用完|耗尽|上限)|每日.{0,8}上限)')
# 供应商业务码：智谱/Z.ai 1113 欠费、1308/1310 用量上限；讯飞 11201 日流控；
# 腾讯 TokenHub 401007/401008 额度用尽；百炼 FreeTierOnly/Arrearage 已由关键词覆盖。
_QUOTA_CODE_RE = re.compile(r'"code"\s*:\s*"?(1113|1308|1310|11201|401007|401008|3036)"?\b')
_RATE_RE = re.compile(r'(?i)(rate.?limit|too many requests|per ?minute|perminute|requests? per minute|tokens? per minute'
                      r'|\brpm\b|\btpm\b|concurren|并发|频率|限流|请求过多|速率)')
_MINUTE_RE = re.compile(r'(?i)(per ?minute|perminute|\brpm\b|\btpm\b|limit_rpm|limit_tpm)')
_DAY_RE = re.compile(r'(?i)(per[ -]?day|perday|\brpd\b|\btpd\b|daily|每日|每天)')
_AUTH_RE = re.compile(r'(?i)(invalid[_ ]?api[_ ]?key|unauthori[sz]ed|authentication|not logged in|login required|please log ?in|身份验证|鉴权|未登录|实名)')


def limit_kind(status, text):
    """把 HTTP 状态和错误正文归为 quota / rate / auth / ''；只返回类别，正文不外泄。"""
    text = text or ''
    if status in (401,) or (status in (None, 403) and _AUTH_RE.search(text) and not _QUOTA_RE.search(text)):
        return 'auth'
    # Gemini 的每分钟限流与日额度都写 “exceeded your current quota”，靠 quotaId 区分：
    # 只出现分钟级标识、没有日级标识时按短时限流处理，避免把分钟限流暂停到第二天。
    if _MINUTE_RE.search(text) and not _DAY_RE.search(text) and status != 402:
        return 'rate'
    if _QUOTA_RE.search(text) or _QUOTA_CODE_RE.search(text) or status == 402:
        return 'quota'
    if status == 429 or _RATE_RE.search(text):
        return 'rate'
    return ''


def retry_after_seconds(value, now=None):
    """Retry-After 可以是秒数，也可以是 HTTP-date（RFC 9110）。无法解析返回 None。"""
    value = (value or '').strip()
    if not value:
        return None
    if re.fullmatch(r'\d+(\.\d+)?', value):
        return math.ceil(float(value))
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None or when.tzinfo is None:
        return None
    return max(0, math.ceil(when.timestamp() - (time.time() if now is None else now)))


def _reset_seconds(value, now=None):
    """x-ratelimit-reset* 的常见写法：秒、'6m0s'/'1h2m3.5s' 时长、Unix 秒或毫秒时间戳。"""
    value = (value or '').strip()
    now = time.time() if now is None else now
    if re.fullmatch(r'\d+(\.\d+)?', value):
        number = float(value)
        if number > 1e12: return max(0, math.ceil(number / 1000 - now))
        if number > 1e9: return max(0, math.ceil(number - now))
        return math.ceil(number)
    match = re.fullmatch(r'(?:(\d+)h)?(?:(\d+)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?', value)
    if value and match and any(match.groups()):
        h, m, sec, ms = match.groups()
        return math.ceil(int(h or 0) * 3600 + int(m or 0) * 60 + float(sec or 0) + int(ms or 0) / 1000)
    return None


def limit_suffix(status, body, headers=None, now=None):
    kind = limit_kind(status, body)
    parts = [' limit=' + kind] if kind else []
    waits = []
    delay = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body or '')
    if kind and delay:
        waits.append(math.ceil(float(delay.group(1))))
    if headers is not None:
        waits.append(retry_after_seconds(headers.get('Retry-After', ''), now))
        if kind:
            for name in ('x-ratelimit-reset', 'x-ratelimit-reset-requests', 'x-ratelimit-reset-tokens'):
                waits.append(_reset_seconds(headers.get(name, ''), now))
    waits = [w for w in waits if w is not None]
    if waits:
        parts.append(' retry_after=' + str(max(waits)))
    return ''.join(parts)


def api_generate(item, prompt):
    model = item['model']; maximum = item.get('max_tokens', 4096)
    protocol = item['protocol']
    if protocol == 'anthropic':
        obj = api_request(item, '/messages', {'model': model, 'max_tokens': maximum,
            'messages': [{'role': 'user', 'content': prompt}]})
        if obj.get('stop_reason') != 'end_turn':
            raise ValueError('Anthropic response incomplete or requires tools')
        content = ''.join(b['text'] for b in obj.get('content', []) if b.get('type') == 'text')
    elif protocol == 'openai-responses':
        obj = api_request(item, '/responses', {'model': model, 'input': prompt, 'max_output_tokens': maximum})
        if obj.get('status') != 'completed':
            raise ValueError('Responses response incomplete')
        content = ''.join(b['text'] for m in obj.get('output', []) for b in m.get('content', []) if b.get('type') == 'output_text')
    else:
        obj = api_request(item, '/chat/completions', {'model': model,
            'messages': [{'role': 'user', 'content': prompt}], item.get('token_parameter','max_tokens'): maximum})
        choice = obj['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('Chat response incomplete or requires tools')
        content = choice['message']['content']
    raw = obj.get('usage', {})
    inp = raw.get('input_tokens', raw.get('prompt_tokens'))
    out = raw.get('output_tokens', raw.get('completion_tokens'))
    # Retain vendor token totals; do not invent subscription dollar costs.
    usage = {'input_tokens': inp, 'output_tokens': out,
             'total_tokens': raw.get('total_tokens', inp + out + (raw.get('cache_read_input_tokens',0) + raw.get('cache_creation_input_tokens',0) if protocol=='anthropic' else 0) if isinstance(inp, int) and isinstance(out, int) else None),
             'model': obj.get('model', model), 'cost_usd': None, 'cost_label': None,
             'source': protocol + ' API', 'cache_read_tokens': raw.get('cache_read_input_tokens'),
             'cache_write_tokens': raw.get('cache_creation_input_tokens')}
    return content, usage


# CLI 只继承运行所需的基础变量与各自配置目录，不把宿主上的其他付费 API Key 带进去，
# 避免 CLI 因环境里的 KEY 改走计费 API。确需的认证变量写进 transport.env_passthrough。
_CLI_ENV_BASE = ('PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TMPDIR', 'TMP', 'TEMP', 'LANG', 'LANGUAGE', 'TERM', 'TZ',
                 '__CF_USER_TEXT_ENCODING', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS',
                 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'no_proxy', 'all_proxy')
_CLI_ENV_PREFIX = ('LC_', 'XDG_')  # 不放 WQ_：自定义渠道的 Key 用 WQ_*_API_KEY
_CLI_ENV_HOME = {'claude': ('CLAUDE_CONFIG_DIR',), 'codex': ('CODEX_HOME',), 'gemini': ('GEMINI_CLI_HOME',),
                 'qwen': ('QWEN_HOME',), 'copilot': ('COPILOT_HOME',), 'opencode': ('OPENCODE_CONFIG', 'OPENCODE_CONFIG_DIR')}
_ENV_NAME = re.compile(r'[A-Z_][A-Z0-9_]{0,63}')


def cli_env(item, source=None):
    source = os.environ if source is None else source
    extra = item.get('env_passthrough') or []
    if not isinstance(extra, list) or any(not isinstance(n, str) or not _ENV_NAME.fullmatch(n) for n in extra):
        raise ValueError('env_passthrough must list environment variable names')
    keep = set(_CLI_ENV_BASE) | set(_CLI_ENV_HOME.get(item.get('kind'), ())) | set(extra)
    return {k: v for k, v in source.items() if k in keep or k.startswith(_CLI_ENV_PREFIX)}


def cli_command(kind, binary, model, prompt, effort=None):
    if kind == 'claude':
        return [binary, '-p', '--model', model] + (['--effort', effort] if effort else []) + ['--output-format', 'json', '--tools', '',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--no-session-persistence', prompt]
    if kind == 'codex':
        return [binary, 'exec', '--model', model] + (['-c', f'model_reasoning_effort="{effort}"'] if effort else []) + ['--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral', '--json', prompt]
    if kind in ('gemini', 'qwen'):
        return [binary, '-p', prompt, '--model', model, '--approval-mode', 'plan', '--output-format', 'json']
    if kind == 'copilot':
        return [binary, '-p', prompt, '--model', model, '--deny-tool', '*', '--disable-builtin-mcps', '--no-custom-instructions', '-s']
    if kind == 'opencode':
        return [binary, 'run', '--model', model, '--format', 'json', prompt]
    raise ValueError('Unsupported CLI adapter')


def cli_response(kind, output):
    if kind == 'copilot':
        return output, {}
    if kind in ('claude', 'gemini', 'qwen'):
        obj = json.loads(output)
        if isinstance(obj, list):
            # qwen（及 claude --verbose）的 json 输出是消息数组，终态在最后一条 type=result。
            results = [e for e in obj if isinstance(e, dict) and e.get('type') == 'result']
            if not results:
                raise ValueError('Missing CLI result event')
            obj = results[-1]
        if not isinstance(obj, dict):
            raise ValueError('Unexpected CLI JSON output')
        if obj.get('is_error') or obj.get('error') or obj.get('subtype', 'success') != 'success':
            raise ValueError('CLI reported failure')
        return obj.get('result', obj.get('response', '')), obj.get('usage') or {}
    events = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
    if kind == 'codex':
        done = [e for e in events if e.get('type') == 'turn.completed']
        if not done:
            raise ValueError('Missing Codex completed terminal event')
        messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed' and e.get('item', {}).get('type') == 'agent_message']
        raw = done[-1].get('usage') or {}
        usage = {'input_tokens': raw.get('input_tokens'), 'output_tokens': raw.get('output_tokens')}
        if isinstance(usage['input_tokens'], int) and isinstance(usage['output_tokens'], int):
            usage['total_tokens'] = usage['input_tokens'] + usage['output_tokens']
        return messages[-1] if messages else '', usage
    if any(e.get('type') == 'error' for e in events):
        raise ValueError('OpenCode reported failure')
    return ''.join(e.get('part', {}).get('text', '') for e in events if e.get('type') == 'text'), {}


def parse_result(content):
    content = content.strip()
    if content.startswith('```') and content.endswith('```'):
        content = content.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    obj = json.loads(content)
    if not isinstance(obj, dict) or obj.get('status') not in ('completed', 'blocked') or not isinstance(obj.get('summary'), str) or not isinstance(obj.get('findings'), list):
        raise ValueError('Expected result object: status, summary, findings')
    return obj


def main():
    # Invoked only by the gated queue, with a frozen, secret-free definition.
    item = json.loads(sys.argv[1]); prompt = Path(sys.argv[2]).read_text()
    prompt += '\nTransport contract: return the entire result.json object as your final response. Do not use tools or write files. No Markdown outside the JSON object.'
    if item['kind'] == 'api':
        content, usage = api_generate(item, prompt)
    else:
        env = cli_env(item)
        if item['kind'] == 'opencode':
            env['OPENCODE_PERMISSION'] = json.dumps({'*': 'deny'})
        proc = subprocess.run(cli_command(item['kind'], item['binary'], item['model'], prompt, item.get('effort')),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
        if proc.returncode:
            # CLI 的输出可能含提示词或账号信息，只保留分类；额度/限流信号交给路由层暂停或退避。
            # stdout 混有模型输出，只取明确的错误事件行（codex/claude 的 JSON 错误在 stdout）。
            errors = [line for line in (proc.stdout or '').splitlines()[-200:]
                      if re.search(r'(?i)"type"\s*:\s*"(error|turn\.failed)"|"is_error"\s*:\s*true|^\s*error\b', line)]
            kind = limit_kind(None, (proc.stderr or '')[-8192:] + '\n' + '\n'.join(errors)[-8192:])
            raise ValueError('Provider CLI failed with exit ' + str(proc.returncode) + (' limit=' + kind if kind else ''))
        content, raw = cli_response(item['kind'], proc.stdout)
        usage = {'source': item['kind'] + ' CLI', 'model': item['model'], 'cost_usd': None,
                 'input_tokens': raw.get('input_tokens'), 'output_tokens': raw.get('output_tokens'),
                 'total_tokens': raw.get('total_tokens')}
    print(json.dumps({'wq_usage': usage}), flush=True)
    obj = parse_result(content)
    with open('result.json', 'x') as f:
        json.dump(obj, f, ensure_ascii=False)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError, AttributeError, IndexError) as exc:
        # Do not echo response fragments, prompts, endpoint credentials or keys.
        print('Provider adapter failed: ' + (str(exc) if type(exc) is ValueError and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__), file=sys.stderr)
        sys.exit(2)
