"""官方免费 API 预设：只收供应商自己提供的免费层 / 免费模型 / 新人额度，全部走现有 OpenAI 兼容 transport。

额度与限制按 VERIFIED_AT 当天的官方文档整理，供应商随时可能调整；这里写的是“用户注册时应当看到什么”，
不是 autowq 的承诺。实际能否调用、剩余多少仍以用户账号为准，额度用尽由路由层的额度暂停处理。
"""

VERIFIED_AT = '2026-09-29'

# quota_reset：额度按固定时刻重置的渠道写明时区与时刻，额度暂停直接等到该时刻；
# 没有公开重置规则的（按并发限流、按月发放但重置日未公开、一次性新人额度）不写，由路由层按间隔复查。
PRESETS = {
    'gemini-free': {
        'region': 'global', 'label': 'Google Gemini API（免费层）',
        'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
        'model': 'gemini-3.8-flash', 'models': ['gemini-3.8-flash', 'gemini-2.5-flash', 'gemini-2.5-flash-lite'],
        'token_parameter': 'max_tokens', 'max_tokens': 8192, 'timeout_s': 300,
        'quota_reset': {'tz': 'America/Los_Angeles', 'at': '00:00', 'period': 'daily'},
        'signup': 'https://aistudio.google.com/apikey',
        'limits': ('免费层有 RPM/TPM/RPD 上限，具体数值只在 AI Studio 显示；每日额度按太平洋时间午夜重置',
                   'Free tier has RPM/TPM/RPD caps shown only in AI Studio; daily quota resets at Pacific midnight'),
        'caveats': ('免费层提交内容会被用于改进 Google 产品；中国大陆与香港不可用',
                    'Free-tier content is used to improve Google products; unavailable in mainland China and Hong Kong'),
        'sources': ['https://ai.google.dev/gemini-api/docs/pricing', 'https://ai.google.dev/gemini-api/docs/rate-limits',
                    'https://ai.google.dev/gemini-api/docs/openai'],
    },
    'openrouter-free': {
        'region': 'global', 'label': 'OpenRouter 免费模型（:free）',
        'base_url': 'https://openrouter.ai/api/v1',
        'model': 'qwen/qwen3.8-27b:free',
        'models': ['qwen/qwen3.8-27b:free', 'nvidia/nemotron-3-super-120b-a12b:free', 'google/gemma-4-31b-it:free'],
        'token_parameter': 'max_tokens', 'max_tokens': 8192, 'timeout_s': 300,
        'quota_reset': {'tz': 'UTC', 'at': '00:00', 'period': 'daily'},
        'signup': 'https://openrouter.ai/settings/keys',
        'limits': ('20 次/分钟；累计购买不足 10 credits 时每天 50 次，满 10 credits 后每天 1000 次；UTC 零点重置',
                   '20 requests/min; 50/day until 10 credits were ever purchased, then 1000/day; resets at UTC midnight'),
        'caveats': ('免费模型会变动或临时不可用，请选定明确的 :free 模型；不要用会随机换模型的 openrouter/free；账户余额为负时免费模型也会返回 402',
                    'Free models change or go offline; pick an explicit :free model, not the random openrouter/free router; a negative balance also blocks free models (402)'),
        'sources': ['https://openrouter.ai/docs/api-reference/limits', 'https://openrouter.ai/docs/api-reference/errors'],
    },
    'zai-free': {
        'region': 'global', 'label': 'Z.ai GLM-4.7-Flash（免费）',
        'base_url': 'https://api.z.ai/api/paas/v4',
        'model': 'glm-4.7-flash', 'models': ['glm-4.7-flash'],
        'token_parameter': 'max_tokens', 'max_tokens': 16384, 'timeout_s': 300,
        'signup': 'https://z.ai/manage-apikey/apikey-list',
        'limits': ('GLM-4.7-Flash 标为免费；并发与每日上限未公开，按并发限流', 'GLM-4.7-Flash is listed as free; concurrency/daily caps are not published'),
        'caveats': ('默认开启思考，max_tokens 已放宽到 16384；与大陆智谱是不同账号与端点', 'Thinking is on by default, so max_tokens is 16384; separate account and endpoint from mainland BigModel'),
        'sources': ['https://docs.z.ai/guides/overview/pricing', 'https://docs.z.ai/api-reference/api-code'],
    },
    'mistral-free': {
        'region': 'global', 'label': 'Mistral La Plateforme（Free 档）',
        'base_url': 'https://api.mistral.ai/v1',
        'model': 'mistral-small-latest', 'models': ['mistral-small-latest', 'mistral-medium-latest'],
        'token_parameter': 'max_tokens', 'max_tokens': 8192, 'timeout_s': 300,
        'signup': 'https://console.mistral.ai/api-keys',
        'limits': ('Free 档每月 10 美元 API 额度，不需要信用卡；限流数值只在控制台显示', 'Free plan: $10/month of API usage, no card; rate limits shown in the console'),
        'caveats': ('默认可能用于改进服务，可在 Admin → Privacy 关闭；429 不区分限流与额度用尽', 'May be used to improve services by default (turn off under Admin → Privacy); 429 does not tell rate limit from exhausted credit'),
        'sources': ['https://mistral.ai/pricing', 'https://docs.mistral.ai/admin/billing-usage/subscriptions'],
    },
    'groq-free': {
        'region': 'global', 'label': 'Groq（免费档，适合短输入）',
        'base_url': 'https://api.groq.com/openai/v1',
        'model': 'openai/gpt-oss-120b', 'models': ['openai/gpt-oss-120b', 'qwen/qwen3.8-27b'],
        'token_parameter': 'max_completion_tokens', 'max_tokens': 4096, 'timeout_s': 180,
        'signup': 'https://console.groq.com/keys',
        'limits': ('约 30 次/分钟、1000 次/天、8K tokens/分钟、20 万 tokens/天（按模型不同）', 'About 30 RPM, 1K RPD, 8K TPM, 200K TPD (varies by model)'),
        'caveats': ('8K tokens/分钟：单次输入加输出超过约 8K 会被拒，长上下文轮次不适用', '8K TPM: a single call above ~8K input+output is refused; not for long-context cycles'),
        'sources': ['https://console.groq.com/docs/rate-limits'],
    },
    'bigmodel-free': {
        'region': 'cn', 'label': '智谱 BigModel GLM-4.7-Flash（永久免费）',
        'base_url': 'https://open.bigmodel.cn/api/paas/v4',
        'model': 'glm-4.7-flash', 'models': ['glm-4.7-flash', 'glm-4-flash-250414', 'glm-z1-flash'],
        'token_parameter': 'max_tokens', 'max_tokens': 16384, 'timeout_s': 300,
        'signup': 'https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys',
        'limits': ('免费模型不限总量，只按并发限流（数值在控制台查看）', 'Free models have no total cap; only concurrency limits (see console)'),
        'caveats': ('高峰期可能返回 1305 过载，会按限流退避重试；只使用列出的免费模型 ID，其他 GLM 模型收费', 'Peak hours may return 1305 overload (retried with backoff); only the listed IDs are free, other GLM models are billed'),
        'sources': ['https://docs.bigmodel.cn/cn/guide/start/pricing', 'https://docs.bigmodel.cn/cn/api/api-code'],
    },
    'siliconflow-free': {
        'region': 'cn', 'label': '硅基流动 SiliconFlow 免费模型',
        'base_url': 'https://api.siliconflow.cn/v1',
        'model': 'Qwen/Qwen3-8B', 'models': ['Qwen/Qwen3-8B', 'THUDM/GLM-4-9B-0414', 'THUDM/GLM-Z1-9B-0414'],
        'token_parameter': 'max_tokens', 'max_tokens': 8192, 'timeout_s': 300,
        'signup': 'https://cloud.siliconflow.cn/account/ak',
        'limits': ('免费模型按固定 RPM/TPM 限流（登录后查看）', 'Free models have fixed RPM/TPM limits (see console)'),
        'caveats': ('须完成实名认证；免费模型为小参数模型且会不定期下线', 'Real-name verification required; free models are small and are retired from time to time'),
        'sources': ['https://docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation', 'https://docs.siliconflow.cn/docs/userguide/faqs/error-code'],
    },
    'spark-lite': {
        'region': 'cn', 'label': '讯飞星火 Spark Lite（免费）',
        'base_url': 'https://spark-api-open.xf-yun.com/v1',
        'model': 'lite', 'models': ['lite'],
        'token_parameter': 'max_tokens', 'max_tokens': 4096, 'timeout_s': 180,
        'quota_reset': {'tz': 'Asia/Shanghai', 'at': '00:00', 'period': 'daily'},
        'signup': 'https://console.xfyun.cn/services/cbm',
        'limits': ('官方标注可免费使用；QPS 与每日上限未公开', 'Listed as free; QPS and daily caps are not published'),
        'caveats': ('输出最多 4096 tokens、能力较弱，复杂 JSON 可能不稳定，建议只作审查兜底；Key 填控制台的 APIPassword', 'Output capped at 4096 tokens and weaker; use as a review fallback; the key is the console APIPassword'),
        'sources': ['https://www.xfyun.cn/doc/spark/HTTP%E8%B0%83%E7%94%A8%E6%96%87%E6%A1%A3.html'],
    },
    'modelscope-free': {
        'region': 'cn', 'label': '魔搭 ModelScope API-Inference',
        'base_url': 'https://api-inference.modelscope.cn/v1',
        'model': 'Qwen/Qwen3.5-35B-A3B', 'models': ['Qwen/Qwen3.5-35B-A3B'],
        'token_parameter': 'max_tokens', 'max_tokens': 8192, 'timeout_s': 300,
        'quota_reset': {'tz': 'Asia/Shanghai', 'at': '00:00', 'period': 'daily'},
        'signup': 'https://www.modelscope.cn/my/myaccesstoken',
        'limits': ('每天发放当日有效的魔粒，每次调用按模型扣 0.5–2 魔粒，约 125–500 次/天', 'Daily credits (valid that day only); each call costs 0.5–2, roughly 125–500 calls/day'),
        'caveats': ('须绑定已实名的阿里云账号；可用模型会动态上下线', 'Requires a real-name-verified Alibaba Cloud account; available models change'),
        'sources': ['https://www.modelscope.cn/docs/model-service/API-Inference/limits'],
    },
    'bailian-trial': {
        'region': 'cn', 'label': '阿里云百炼（新人免费额度，限时）',
        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        'model': 'qwen3.7-flash', 'models': ['qwen3.7-flash', 'qwen3.5-flash', 'qwen-flash'],
        'token_parameter': 'max_tokens', 'max_tokens': 8192, 'timeout_s': 300,
        'trial': True,
        'signup': 'https://bailian.console.aliyun.com/?tab=model#/api-key',
        'limits': ('新人每个模型 100 万 tokens，90 天有效，仅北京地域', 'New users: 1M tokens per model for 90 days, Beijing region only'),
        'caveats': ('必须先在控制台打开“免费额度用完即停”，否则额度用完后会自动按量扣费；到期后不再免费', 'Turn on "stop when free quota is used up" first, or usage is billed after the free quota; not free after expiry'),
        'sources': ['https://help.aliyun.com/zh/model-studio/new-free-quota', 'https://help.aliyun.com/zh/model-studio/error-code'],
    },
}


def listing(region=None, lang='zh'):
    index = 0 if lang == 'zh' else 1
    rows = []
    for name, item in PRESETS.items():
        if region and item['region'] != region:
            continue
        rows.append({'preset': name, 'region': item['region'], 'label': item['label'], 'model': item['model'],
                     'models': item['models'], 'base_url': item['base_url'], 'signup': item['signup'],
                     'limits': item['limits'][index], 'caveats': item['caveats'][index],
                     'trial': bool(item.get('trial')), 'quota_reset': item.get('quota_reset'),
                     'sources': item['sources'], 'verified_at': VERIFIED_AT})
    return rows


def key_env(name):
    return 'WQ_' + name.upper().replace('-', '_') + '_API_KEY'


def api_spec(preset, model=None, name=None):
    """返回可直接交给 providers.api_definition 校验的传输定义，以及写在渠道层的额度重置规则。"""
    if preset not in PRESETS:
        raise ValueError('未知免费预设 / Unknown free preset: ' + preset)
    item = PRESETS[preset]
    from .providers import api_definition
    api = api_definition('openai', model or item['model'], item['base_url'], key_env(name or preset))
    api['token_parameter'] = item['token_parameter']
    api['max_tokens'] = item['max_tokens']
    api['timeout_s'] = item['timeout_s']
    from .provider_runtime import validate_api
    validate_api(api)
    return api, item.get('quota_reset')
