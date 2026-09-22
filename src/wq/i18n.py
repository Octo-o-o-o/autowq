"""Locale selection without changing process locale or environment."""
import locale
import os


def default_language(environ=None):
    env = os.environ if environ is None else environ
    value = next((env[k] for k in ('LC_ALL', 'LC_MESSAGES', 'LANGUAGE', 'LANG') if env.get(k)), None)
    if value is None:
        try: value = locale.getlocale()[0] or ''
        except (ValueError, locale.Error): value = ''
    return 'zh' if value.split(':')[0].lower().replace('-', '_').startswith('zh') else 'en'


def text(lang, zh, en):
    return zh if lang == 'zh' else en

HELP_EN = {
'界面语言 / Interface language':'Interface language',
'WorldQuant 研究试点本地工具（离线优先）':'Local WorldQuant research workflow (offline first)',
'config.json 路径，默认 ./config/config.json':'Configuration path (default: ./config/config.json)',
'覆盖 SQLite 路径（测试用）':'Override SQLite path (for testing)',
'首次使用：选择本地CLI、模型和角色 / first-run wizard':'First-run wizard: language, models, roles and login',
'BRAIN本地会话、接口核验与单次模拟队列':'BRAIN login, access checks, simulation and submission queues',
'持续研究：状态/启动/停止补充任务':'Inspect, start or stop continuous research',
'核验证据声明；不把声明完整视为平台就绪':'Validate evidence declarations (not proof of platform readiness)',
'查看或切换整套路由预设（下一项任务生效）':'Inspect or select routing presets for new tasks',
'临时停用/恢复某个渠道，不修改套餐':'Disable or enable a provider locally; does not change subscription',
'创建按预设路由的本地任务':'Create a locally routed task',
'明确筛选的输入目录，将复制给模型':'Explicitly selected input directory to copy to the model',
'环境/预算/账号就绪检查':'Check environment, budgets and account prerequisites',
'运行 bin --version（只读探测）':'Run binary --version (read-only probe)',
'创建 private_dir(0700)':'Create private directory with mode 0700',
'校验契约文件':'Validate a contract document',
'导入研究卡 → 假设族与候选':'Import a research card into families and candidates',
'导入模拟结果（manual-import）':'Import simulation results',
'声明确认为真实（非合成）结果':'Confirm these are real, non-synthetic results',
'入队任务 simulation|submission|agent_call|reconcile|import':'Enqueue a simulation, submission, agent call, reconciliation or import',
'JSON 字符串或 @file':'JSON string or @file',
'kind=import 时确认文档为真实结果（非合成）':'For import tasks, confirm real rather than synthetic results',
'领取并处理一个到期任务（单并发）':'Claim and process one due task (single concurrency)',
'租约秒数':'Lease duration in seconds',
'一页报告':'Generate a research and accounting report',
'机器可读状态摘要':'Machine-readable status summary',
'暂停：禁止新任务并终止在途本地调用':'Pause queue and terminate active local model calls',
'恢复（存在 UNKNOWN 需先对账或 --force）':'Resume; reconcile UNKNOWN tasks first or explicitly use --force',
'列出 UNKNOWN 并对账':'List and reconcile UNKNOWN requests',
'UNKNOWN BRAIN请求经本人核实的Alpha ID；仅恢复GET并校验结果':'Verified Alpha ID for an UNKNOWN BRAIN request; resume GET only',
'登记真实现金账本（唯一收入口径）':'Record payable or received cash (not simulated income)',
'必填：到账/应付证据说明':'Required evidence of received/payable cash',
'登记费用/额度消耗':'Record expenses or quota use',
'登记账号阶段（需证据）':'Record account stage with evidence',
'设置预算闸门（写回 config.json）':'Set local budget controls in config.json',
'单次 gated 模型调用（默认阻断）':'One gated model call (blocked by default)',
'models.<agent> 已配置项，如 grok|devin':'Configured models.<agent> name, such as grok or devin',
'逗号分隔的预期产物相对路径':'Comma-separated expected relative artifact paths',
'确认已获本次调用授权':'Confirm authorization for this invocation',
'中文任务进度（--json 输出原始数据）':'Task progress (Chinese display; --json for structured output)',
'保留机器可读 JSON 格式':'Output machine-readable JSON',
'已完成的提交研究验收JSON；不可使用模板占位':'Completed submission research review JSON, not a placeholder',
}


def localize_help(parser, lang):
    import argparse
    if lang != 'en': return parser
    parser.description=HELP_EN.get(parser.description,parser.description)
    for action in parser._actions:
        action.help=HELP_EN.get(action.help,action.help)
        if isinstance(action,argparse._SubParsersAction):
            for item in action._choices_actions: item.help=HELP_EN.get(item.help,item.help)
            for child in action.choices.values():localize_help(child,lang)
    return parser


def preferred_language(config_path='config/config.json'):
    import json
    from pathlib import Path
    try:
        value=json.loads(Path(config_path).read_text()).get('ui',{}).get('language')
        if value in ('zh','en'): return value
    except (OSError,ValueError,TypeError,AttributeError): pass
    return default_language()
