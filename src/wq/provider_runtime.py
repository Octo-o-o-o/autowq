"""Single text/JSON inference adapter; also copied into isolated CLI runtimes."""
import json
import os
from pathlib import Path
import subprocess
import sys
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
    if u.scheme != 'https' and not (u.scheme == 'http' and u.hostname in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('Use HTTPS (HTTP allowed only for loopback endpoints)')
    if not item.get('model'):
        raise ValueError('Explicit model required')
    if type(item.get('max_tokens',4096)) is not int or not 1 <= item.get('max_tokens', 4096) <= 131072:
        raise ValueError('max_tokens must be 1..131072')


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
        # Bodies/URLs can echo keys; never log them.
        retry = e.headers.get('Retry-After', '')
        suffix = ' retry_after=' + retry if retry.isdigit() else ''
        raise ValueError('API HTTP ' + str(e.code) + suffix) from None
    except urllib.error.URLError:
        raise ValueError('API transport failed; check endpoint/network') from None


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


def cli_command(kind, binary, model, prompt):
    if kind == 'claude':
        return [binary, '-p', '--model', model, '--output-format', 'json', '--tools', '',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--no-session-persistence', prompt]
    if kind == 'codex':
        return [binary, 'exec', '--model', model, '--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral', '--json', prompt]
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
        if obj.get('is_error') or obj.get('error'):
            raise ValueError('CLI reported failure')
        return obj.get('result', obj.get('response', '')), obj.get('usage', {})
    events = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
    if kind == 'codex':
        if not any(e.get('type') == 'turn.completed' for e in events):
            raise ValueError('Missing Codex completed terminal event')
        messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed' and e.get('item', {}).get('type') == 'agent_message']
        return messages[-1] if messages else '', {}
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
        env = dict(os.environ)
        if item['kind'] == 'opencode':
            env['OPENCODE_PERMISSION'] = json.dumps({'*': 'deny'})
        proc = subprocess.run(cli_command(item['kind'], item['binary'], item['model'], prompt),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)
        if proc.returncode:
            raise ValueError('Provider CLI failed with exit ' + str(proc.returncode))
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
    except (ValueError, KeyError, OSError, TypeError) as exc:
        # Do not echo response fragments, prompts, endpoint credentials or keys.
        print('Provider adapter failed: ' + (str(exc) if type(exc) is ValueError and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__), file=sys.stderr)
        sys.exit(2)
