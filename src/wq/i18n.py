"""Locale selection without changing process locale or environment.

语言偏好三级：`--lang` 参数（会话内）→ config.json 的 ui.language（'zh'/'en'/'auto'）
→ 环境自动检测（zh* 为中文，其余一律英文）。账本里的历史消息与轮次结局由 autopilot
以中文写入；英文界面用 translate() 在显示层翻译，已知前缀之外的自由文本保持原文。
"""
import json
import locale
import os
import sys
import re
from pathlib import Path


def default_language(environ=None):
    env = os.environ if environ is None else environ
    value = next((env[k] for k in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG') if env.get(k)), None)
    if value is None and sys.platform == 'win32':
        # Windows 的 getlocale() 返回 'Chinese (Simplified)_China' 这类名称；以系统界面语言为准（主语言 0x04 为中文）。
        try:
            import ctypes
            return 'zh' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x04 else 'en'
        except (AttributeError, OSError):
            pass
    if value is None:
        try: value = locale.getlocale()[0] or ''
        except (ValueError, locale.Error): value = ''
    value = value.split(':')[0].lower().replace('-', '_')
    return 'zh' if value.startswith(('zh', 'chinese')) else 'en'


def text(lang, zh, en):
    return zh if lang == 'zh' else en


def stored_language(config_path):
    """config.json 的 ui.language 原值：'zh' | 'en' | 'auto'；未配置或不可读返回 None。"""
    try:
        value = json.loads(Path(config_path).read_text(encoding='utf-8')).get('ui', {}).get('language')
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return value if value in ('zh', 'en', 'auto') else None


def preferred_language(config_path='config/config.json'):
    """显示用生效语言：显式 zh/en 直接采用，'auto'/未配置走环境检测。"""
    pref = stored_language(config_path)
    return pref if pref in ('zh', 'en') else default_language()


def write_language(config_path, value):
    """持久化 ui.language（'zh'/'en'/'auto'）；config 不存在时返回 False。"""
    from .util import write_json
    if value not in ('zh', 'en', 'auto'):
        raise ValueError('language 只接受 zh/en/auto')
    try:
        data = json.loads(Path(config_path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    data.setdefault('ui', {})['language'] = value
    mode = os.stat(config_path).st_mode & 0o777
    write_json(config_path, data)
    os.chmod(config_path, mode)
    return True


# ---------- 账本消息/结局的显示层英文翻译 ----------
# autopilot 以中文把 message/outcome 写入账本；英文界面按下表翻译，
# 未知自由文本（模型摘要、诊断串）保持原文。中文原文改动必须同步此表。

_EXACT_EN = {
    '尚未启用': 'Not enabled yet',
    '菜单栏退出': 'Quit from the menu bar',
    '菜单栏手动暂停': 'Paused from the menu bar',
    'menu:quit': 'Quit from the menu bar',
    'menu:pause': 'Paused from the menu bar',
    '用户取消当前轮次': 'Current cycle cancelled',
    '用户立即停止': 'Stopped immediately by the user',
    '已立刻停止自动研究': 'Automatic research stopped immediately',
    '当前轮次结束后停止自动研究': 'Automatic research stops when the current cycle ends',
    '没有进行中的轮次，已停止自动研究': 'No cycle is running; automatic research is stopped',
    '当前轮次已结束，自动研究已停止': 'The current cycle ended; automatic research is stopped',
    'menu:stop-now': 'All tasks stopped immediately',
    'menu:stop-after-cycle': 'Stopped after the current cycle',
    '尚未启动': 'Not started',
    '等待现有任务完成': 'Waiting for existing tasks to finish',
    '等待UNKNOWN对账完成': 'Waiting for UNKNOWN reconciliation',
    '等待历史/本轮真实结果资料回填；完成后继续研究': 'Waiting for real-result feedback data (past and current cycles); research resumes once complete',
    '存在UNKNOWN，完成对账前不派发新的研究模拟': 'UNKNOWN items exist; no new research simulations until reconciled',
    '自动补充任务已停用；现有在途任务单独对账': 'Automatic research is off; in-flight tasks are reconciled individually',
    '自动补充任务已启用，等待本地调度': 'Automatic research enabled; waiting for the local scheduler',
    '双环路冻结基线未审批': 'the dual-loop frozen baseline is not approved',
    '双环路学习授权已到期': 'the dual-loop learning authorization has expired',
    '等待足以改变决策的新证据': 'waiting for new evidence that could change decisions',
    '等待 UNKNOWN 对账完成': 'waiting for UNKNOWN reconciliation to finish',
    '研究框架任务排队中': 'a research-framework task is queued',
    '研究框架工作项已入队': 'a research-framework work item has been enqueued',
    '学习/贡献维护进行中': 'learning/contribution maintenance in progress',
    '已登记材料不可读或已变更': 'registered material is unreadable or has changed',
    '发现额度暂尽：等待可改变观察的新证据登记': 'discovery allowance spent; waiting for new evidence registration that changes the observation',
    '有界发现进行中': 'bounded discovery in progress',
    '全部任务已暂停': 'all tasks paused',
    '已停止创建新轮次；现有任务继续执行，全部暂停用 wq pause': 'No new cycles will be created; existing tasks continue. Use wq pause to pause everything',
    '已请求立刻运行一轮；仍需通过授权、预算、平台冷却及队列闸门': 'Immediate run requested; still gated by authorization, budget, platform cooldown and the queue',
    '备选提交': 'Queued to submit',
    '已登记立刻接下一轮：当前轮次结束后不再等待间隔。在途任务、授权、预算和平台冷却仍然有效':
        'Immediate follow-up registered: the next cycle starts when the current one ends, without the usual wait. In-flight work, authorization, budget and platform cooldown still apply',
    '模型已知花费': 'Known model spend',
    '回测已入账，等待程序收集PnL/年度表现并诊断': 'Backtest recorded; collecting PnL/yearly performance and diagnosing',
    '授权已到期：不启动新研究或模拟；更新预算/有效期后自动继续': 'Authorization expired: no new research or simulations; resumes automatically once budget/validity is updated',
    '达到本次累计研究轮数上限；停止新轮次，等待检查结果': 'Total cycle limit reached; no new cycles, awaiting review results',
    '达到UTC日研究轮数上限；次日自动继续': 'Daily (UTC) cycle limit reached; resumes the next day',
    '达到UTC日研究轮数上限；最早次日继续，仍受会话/授权/平台额度约束': 'Daily (UTC) cycle limit reached; resumes no earlier than tomorrow, still subject to session/authorization/platform quota',
    '达到自动研究周模拟上限（含变体）；下周预算有效时自动继续': 'Weekly simulation limit reached (variants included); resumes next week while the budget is valid',
    '达到平台本地周派发上限；下周自动检查': 'Platform weekly dispatch limit reached; checked again next week',
    '需要至少两个可用渠道完成研究与审查；配置/额度恢复后自动继续': 'At least two available providers are required for research and review; resumes automatically when configuration/quota recovers',
    '自动研究周预算已耗尽': 'Weekly simulation budget exhausted',
    '存在 UNKNOWN 待对账，不能立即运行': 'UNKNOWN items pending reconciliation; cannot run immediately',
    '已有研究轮次在途，请等待完成后再运行下一轮': 'A research cycle is already in flight; wait for it to finish before requesting the next',
    '已达到累计研究轮数上限': 'Total cycle limit already reached',
    '模型审查拒绝，不回测': 'Model review rejected; no backtest',
    '模型审查拒绝': 'Model review rejected',
    '审查依据待复核': 'Review evidence pending re-check',
    '研究范围配置改变，本轮不再派发': 'Research scope configuration changed; nothing dispatched this cycle',
    '筛选通过，留待进一步验证（不提交）': 'Passed screening; kept for further verification (not submitted)',
    '筛选未通过，归档': 'Failed screening; archived',
    '检查未齐，归档待核实': 'Checks incomplete; archived pending verification',
    '质量待核实，归档': 'Quality unverified; archived',
    '质量待核实': 'Quality unverified',
    '筛选通过': 'Passed screening',
    '筛选未通过': 'Failed screening',
    '检查未齐': 'Checks incomplete',
    '通过': 'Passed',
    '未通过': 'Failed',
    '待核实': 'Unverified',
    'CLI估算（不是供应商账单）': 'CLI estimate (not the vendor bill)',
    '未知（Devin 当前没有可核对的美元单价）': 'unknown (no verifiable Devin unit price)',
    '未调用': 'No call',
    # 渠道路由失败（写入任务 last_error 展示）
    '预设中的渠道均不可用或预算已耗尽；不会自动充值':
        'No provider in the preset is available or the budget is exhausted; no auto top-up',
    '本次调用目录已存在，需核对上次尝试，拒绝覆盖':
        'The call directory already exists; verify the previous attempt — refusing to overwrite',
    '排除提案渠道后没有可用审查渠道':
        'No review provider left after excluding the proposal provider',
    '请提供明确筛选的输入目录，不能使用整个项目或私有目录':
        'Provide an explicitly screened input directory; the whole project or private directories are not allowed',
    '未知任务角色': 'Unknown task role',
    # reconcile
    '正式提交只能用 wq brain reconcile-submit ALPHA_ID 读取官方证据，不能手填accepted':
        'Official submissions must be reconciled via wq brain reconcile-submit ALPHA_ID with official evidence; hand-entered accepted is not allowed',
    '--alpha-id 仅适用于 BRAIN 模拟任务': '--alpha-id applies to BRAIN simulation tasks only',
    'BRAIN对账需要持久化请求记录及 --note 核实依据':
        'BRAIN reconciliation needs a persisted request record and a --note stating how you verified it',
    'Alpha ID 只能包含ASCII字母和数字': 'Alpha IDs may contain ASCII letters and digits only',
    '--alpha-id 只能与 accepted 一起使用': '--alpha-id can only be combined with accepted',
    'Alpha ID 与已保存回执不一致': 'The Alpha ID does not match the saved receipt',
    '缺少回执；需从官方页面核实 --alpha-id，不能仅凭 accepted 记成功':
        'No receipt on file; verify --alpha-id against the official page — accepted alone is not success',
    '已有真实入账结果，不能宣称请求丢失或被拒绝':
        'A real result is already recorded; the request cannot be claimed lost or rejected',
    # brain_submission
    'Alpha ID格式无效': 'Invalid Alpha ID format',
    '仅允许有真实API证据的研究Alpha；教学/手工快照不能直接提交':
        'Only research Alphas with real API evidence qualify; tutorial/manual snapshots cannot be submitted directly',
    '教学Alpha不得进入正式提交队列': 'Tutorial Alphas must not enter the formal submission queue',
    '缺真实候选': 'Missing a real candidate',
    '本地Alpha证据与账本不一致': 'Local Alpha evidence does not match the ledger',
    '缺提交研究验收文档': 'Missing the submission review document',
    '提交验收未绑定Alpha及其完整设置': 'The review is not bound to the Alpha and its full settings',
    '尚未通过提交验收': 'Submission review not passed yet',
    '缺验收人/渠道': 'Missing reviewer/provider in the review',
    '提交验收过期或来自未来': 'The review is expired or timestamped in the future',
    'Alpha存在明确FAIL，禁止提交；不会为验收流程强行提交':
        'The Alpha has an explicit FAIL; submission blocked — never forced through for the review flow',
    'brain_submission.enabled未启用': 'brain_submission.enabled is off',
    '没有该Alpha的提交任务': 'No submission task for this Alpha',
    'Alpha与原提交不一致': 'The Alpha differs from the original submission',
    # importer
    '同配置已有不同 synthetic 类型的候选，拒绝混合真实与合成证据':
        'A candidate with a different synthetic flag already exists for this configuration; real and synthetic evidence cannot be mixed',
    'remote_id 已属于另一候选或证据类型，拒绝错误关联':
        'remote_id already belongs to another candidate or evidence type; refusing a wrong association',
    # brain_jobs
    '只支持REGULAR模拟': 'Only REGULAR simulations are supported',
    '缺少明确表达式': 'Missing an explicit expression',
    '缺少已核验settings': 'Missing verified settings',
    'brain_api.enabled未启用，等待认证和真实接口核验':
        'brain_api.enabled is off; waiting for authentication and a real API check',
    '需明确列出已核验的字段': 'Verified fields must be listed explicitly',
    '需核验过的字段和设置证据': 'Verified fields and settings evidence are required',
    '缺证据来源': 'Missing evidence provenance',
    '需声明教学/研究验证用途': 'A tutorial/research verification purpose must be declared',
    # brain_client
    '拒绝离开 BRAIN 官方 API origin': 'Refusing to leave the official BRAIN API origin',
    'BRAIN 会话文件权限必须为0600且不能是符号链接':
        'The BRAIN session file must have mode 0600 and must not be a symlink',
    '网络结果不明；POST不得自动重发，GET稍后继续':
        'Network outcome unknown; POST is never auto-replayed, GET may continue later',
    '读取响应中断，未自动重发': 'Response reading interrupted; not auto-replayed',
    'BRAIN认证/权限未通过': 'BRAIN authentication/permission failed',
    'BRAIN限流': 'BRAIN rate limited',
    'BRAIN响应不是有效JSON': 'The BRAIN response is not valid JSON',
    '没有取得可复用会话，未保存登录': 'No reusable session obtained; login not saved',
    'Keychain自动登录仅支持macOS；请执行 wq brain login':
        'Keychain auto-login is macOS-only; run wq brain login',
    '缺少BRAIN邮箱；设置 WQ_BRAIN_EMAIL 或 brain_api.auto_login_email':
        'BRAIN email missing; set WQ_BRAIN_EMAIL or brain_api.auto_login_email',
    '无法读取macOS Keychain；请执行 wq brain keychain-save':
        'Cannot read the macOS Keychain; run wq brain keychain-save',
    'Keychain中没有BRAIN凭据；请执行 wq brain keychain-save':
        'No BRAIN credentials in the Keychain; run wq brain keychain-save',
    'BRAIN认证/权限未通过；需本人完成人机/身份验证':
        'BRAIN authentication/permission failed; complete the human/identity verification yourself',
    'Keychain凭据保存仅支持macOS': 'Keychain credential storage is macOS-only',
    'BRAIN邮箱不能为空': 'The BRAIN email cannot be empty',
    '无法写入macOS Keychain': 'Cannot write to the macOS Keychain',
    'macOS Keychain未保存BRAIN凭据': 'No BRAIN credentials saved in the macOS Keychain',
    # routing 配置校验
    'profiles.json 缺 providers/presets/default': 'profiles.json is missing providers/presets/default',
    'provider 名称只允许字母、数字、下划线和短横线':
        'Provider names may contain letters, digits, underscores and hyphens only',
}

_PATTERN_EN = (
    (r'^本轮结束：(.*)；下一轮由本地调度自动领取$', r'Cycle finished: \1; the next cycle will be claimed by the local scheduler'),
    (r'^本轮结束：(.*)；已按立刻运行跳过间隔$', r'Cycle finished: \1; the interval was skipped because an immediate run was requested'),
    (r'^本轮结束：(.*)；单轮请求已完成，自动运行未开启$', r'Cycle finished: \1; one-off request complete, continuous research is off'),
    (r'^需要对账：(\S+?)结果不明；本轮冻结，不重发、不新开轮次$', r'Reconciliation needed: \1 has an unknown outcome; this cycle is frozen — no retries, no new cycles'),
    (r'^需要恢复已有平台请求：(\S+?)；不会新发模拟$', r'Existing platform request to resume: \1; no new simulations will be sent'),
    (r'^需要处理自动研究错误：(.*)$', r'Automatic research error needs attention: \1'),
    (r'^自动研究待命：(.*)$', r'Automatic research standing by: \1'),
    (r'^全部任务已暂停：(.*)$', r'All tasks paused: \1'),
    (r'^等待下一轮 (\S+)$', r'Waiting for the next cycle at \1'),
    (r'^平台要求等待至 (\S+?)；到期后自动继续$', r'Platform requires waiting until \1; resumes automatically afterwards'),
    (r'^平台限流冷却至 (\S+?)；冷却结束再检查，非每日只能运行一次$', r'Platform rate-limit cooldown until \1; checked again after cooldown — not a once-per-day limit'),
    (r'^第(\d+)轮已自动创建；由本地队列执行$', r'Cycle \1 created automatically; executed by the local queue'),
    (r'^第(\d+)轮：已安排唯一补充尝试；原结果保留，额外调用上限1次$', r'Cycle \1: one fallback attempt scheduled; the original result is kept, one extra call at most'),
    (r'^第(\d+)轮 (\S+?)：等待现有任务；不会重复派发$', r'Cycle \1 (\2): waiting for existing tasks; nothing re-dispatched'),
    (r'^(.*)｜预警：(.*)$', r'\1 | Warning: \2'),
    (r'^任务未完成：(.*)$', r'Task incomplete: \1'),
    (r'^反馈资料未完成：(.*)$', r'Feedback data incomplete: \1'),
    (r'^输入或验收错误：(.*)$', r'Input or acceptance error: \1'),
    (r'^模型主动放弃：(.*)$', r'Model abandoned the task: \1'),
    (r'^模型执行失败：(.*)$', r'Model execution failed: \1'),
    (r'^审查依据待复核，不回测：(.*)$', r'Review evidence needs re-checking, no backtest: \1'),
    (r'^按最近24小时速率（(\d+)次/日），周预算约在 (.+?) 耗尽，早于本周结束/授权到期；如需持续运行请放大 autopilot.max_simulations_per_week$',
     r'At the recent rate (\1/day) the weekly budget runs out around \2, before the week or authorization ends; raise autopilot.max_simulations_per_week to keep running'),
    # 带参数的异常消息（名称/ID/状态代入后匹配）
    (r'^未知预设 (\S+)$', r'Unknown preset \1'),
    (r'^当前预设 (\S+) 已不存在；先选择有效预设$', r'The current preset \1 no longer exists; choose a valid preset first'),
    (r'^(\S+): argv 必须为字符串数组$', r'\1: argv must be an array of strings'),
    (r'^(\S+): executable 必须为绝对路径，避免 PATH 同名冲突$', r'\1: executable must be an absolute path to avoid PATH shadowing'),
    (r'^(\S+): timeout_s 超出范围$', r'\1: timeout_s out of range'),
    (r'^(\S+): 当前契约固定为首次 \+ 3 次重试$', r'\1: the contract is fixed at first attempt + 3 retries'),
    (r'^(\S+): retry_delays_s 需三个 0\.\.3600 秒的数值$', r'\1: retry_delays_s needs three values in 0..3600 seconds'),
    (r'^(\S+)/(\S+): 空或重复的 provider 链$', r'\1/\2: empty or duplicate provider chain'),
    (r'^(\S+)/(\S+): 未定义的 provider$', r'\1/\2: undefined provider'),
    (r'^BRAIN HTTP (\d+)；未自动重发或跟随重定向$', r'BRAIN HTTP \1; not auto-replayed and no redirects followed'),
    (r'^BRAIN HTTP (\d+)，请求被拒绝；请核对官方接口/参数$', r'BRAIN HTTP \1, request rejected; check the official API/parameters'),
    (r'^BRAIN全局冷却未结束：(.*)$', r'BRAIN global cooldown not over: \1'),
    (r'^缺实质研究验收依据: (.*)$', r'Missing substantive review evidence: \1'),
    (r'^账本配置与请求不一致: (.*)$', r'Ledger configuration differs from the request: \1'),
    (r'^(.+) 已有 (\d+)/(\d+) 个配置；参数微调不产生新族，达到上限后需先评审再决定是否扩展$',
     r'\1 already has \2/\3 configurations; parameter tweaks do not create a new family — review first before extending at the cap'),
)


def translate(value, lang):
    """把账本中的已知中文消息/结局按界面语言翻译；未知文本原样返回。"""
    if lang == 'zh' or not value or not isinstance(value, str):
        return value
    if value in _EXACT_EN:
        return _EXACT_EN[value]
    for pattern, template in _PATTERN_EN:
        match = re.match(pattern, value)
        if match:
            result = template
            # 捕获组递归翻译：嵌套消息（如“本轮结束：<结局>；下一轮…”）的内层结局一并翻译。
            for index, group in enumerate(match.groups(), 1):
                result = result.replace(f'\\{index}', translate(group, lang) if group else '')
            return result
    return value
