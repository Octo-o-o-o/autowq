"""低强度 BRAIN HTTP 传输；密码不落盘，POST 不自动重发。"""
import base64
import datetime as dt
from email.utils import parsedate_to_datetime
import http.cookiejar
import http.client
import json
import math
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from .errors import AdapterError

ORIGIN = 'https://api.worldquantbrain.com'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def safe_url(value):
    url = urllib.parse.urljoin(ORIGIN + '/', value)
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.netloc != 'api.worldquantbrain.com' or p.username or p.fragment:
        raise ValueError('拒绝离开 BRAIN 官方 API origin')
    return url

def retry_delay(value, default=60):
    if not value:
        return default
    try:
        delay = float(value)
        return max(default, delay) if math.isfinite(delay) else default
    except ValueError:
        try:
            return max(default, (parsedate_to_datetime(value)-dt.datetime.now(dt.timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            return default

class BrainClient:
    def __init__(self, private_dir):
        self.root = Path(private_dir)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root/'brain-session.cookies'
        self.jar = http.cookiejar.LWPCookieJar(str(self.path))
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_mode & 0o077:
                raise ValueError('BRAIN 会话文件权限必须为0600且不能是符号链接')
            self.jar.load(ignore_discard=True, ignore_expires=False)
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, method, path, body=None, auth=None):
        url = safe_url(path)
        headers = {'Accept':'application/json', 'User-Agent':'WorldQuant-local-research/1.0'}
        if auth:
            headers['Authorization']='Basic '+base64.b64encode((auth[0]+':'+auth[1]).encode()).decode()
        encoded = None if body is None else json.dumps(body).encode()
        if encoded is not None:
            headers['Content-Type']='application/json'
        req = urllib.request.Request(url, data=encoded, headers=headers, method=method)
        try:
            response = self.opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        except (OSError, urllib.error.URLError):
            kind = AdapterError.UNKNOWN_REMOTE if method == 'POST' else AdapterError.NETWORK
            raise AdapterError(kind, '网络结果不明；POST不得自动重发，GET稍后继续') from None
        status = response.status
        result_headers = {k.lower():v for k,v in response.headers.items()}
        try:
            raw = response.read(8*1024*1024)
        except (OSError, http.client.HTTPException):
            raise AdapterError(AdapterError.UNKNOWN_REMOTE if method == 'POST' else AdapterError.NETWORK,
                               '读取响应中断，未自动重发') from None
        finally:
            response.close()
        if status in (401,403):
            challenge = result_headers.get('www-authenticate','')
            raise AdapterError(AdapterError.AUTH, 'BRAIN认证/权限未通过'+('，需本人完成人机/身份验证' if challenge else ''))
        if status == 429:
            raise AdapterError(AdapterError.RATE_LIMIT, 'BRAIN限流', retry_delay(result_headers.get('retry-after')))
        if status >= 500 or 300 <= status < 400:
            raise AdapterError(AdapterError.UNKNOWN_REMOTE if method == 'POST' else AdapterError.NETWORK,
                               f'BRAIN HTTP {status}；未自动重发或跟随重定向')
        if status >= 400:
            raise AdapterError(AdapterError.POLICY, f'BRAIN HTTP {status}，请求被拒绝；请核对官方接口/参数')
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            raise AdapterError(AdapterError.UNKNOWN_REMOTE if method == 'POST' else AdapterError.NETWORK,
                               'BRAIN响应不是有效JSON') from None
        return status, result_headers, data

    def login(self, username, password):
        status, _, data = self.request('POST','/authentication',auth=(username,password))
        if status not in (200,201) or not list(self.jar):
            raise AdapterError(AdapterError.AUTH,'没有取得可复用会话，未保存登录')
        temp = self.path.with_suffix('.tmp')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600);os.close(fd)
        os.chmod(temp,0o600)
        self.jar.save(str(temp),ignore_discard=True,ignore_expires=False)
        os.replace(temp,self.path)
        return {'authenticated':True,'password_saved':False,'session_file':str(self.path)}
