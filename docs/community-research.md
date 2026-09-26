# 社区项目调研：可借鉴的外部经验（2026-09-26）

调研范围：WorldQuant BRAIN 生态的开源工具、学术系统（LLM/RL 挖 alpha）、平台规则与社区经验帖。
结论先行：**平台机制层面有三个未利用的乘数（地区、delay-0、主题/Power Pool），研究循环层面最有价值的借鉴是 AgonAlpha 的"再执行式对抗审查"与 wqb 的 multi-simulation 批量模拟**。我们已有的预登记/审查/反馈架构在社区项目里属于先进水平，缺口主要在"平台积分机制利用"和"模拟额度效率"。

## 一、社区项目全景

### 平台 API 工具（工程效率）

| 项目 | 定位 | 对我们的价值 |
|---|---|---|
| [wqb](https://pypi.org/project/wqb/)（PyPI，维护活跃） | BRAIN 官方风格 Python 客户端 | **multi-simulation**（`to_multi_alphas(alphas, 10)` 每 POST 打包 10 个）、并发模拟（顾问权限 3 并发）、`check(alpha_id)` 提交前检查、`filter_alphas` 按 sharpe/fitness/turnover 筛自己未提交的 alpha 池、`patch_properties` 给 alpha 打主题标签、`search_datasets/search_fields` 支持 `valueScore`/`userCount` 排序找冷门数据、持久会话自动重认证 |
| [wqbkit](https://libraries.io/pypi/wqbkit) | Alpha 全生命周期工具箱 | de-correlation 步骤（与已提交池去相关的变体生成）、遗传迭代、Osmosis 竞赛分配 |
| [brain_viewer](https://github.com/justinhuang0208/brain_viewer)、[WQ-Brainn](https://github.com/dige04/WQ-Brainn)、[pyworldquant](https://pypi.org/project/pyworldquant) | GUI/API 封装 | 参考价值低；我们已有完整客户端与账本 |
| [worldquant-brain-simulator](https://github.com/efjerryyang/worldquant-brain-simulator) | 离线回测模拟器 | 作者自注与平台结果有偏差，早期阶段；不引入，但"离线预筛"思路见 worldquant-miner 条 |
| [wqb_cli](https://github.com/untuitivist/wqb_cli) / wqb-mcp | CLI/MCP 封装 | 无新增能力 |

### LLM 自动研究系统（研究循环）

| 项目 | 核心机制 | 对我们的价值 |
|---|---|---|
| [AgonAlpha](https://www.alphaxiv.org/abs/AgonAlpha)（2026-08，arXiv） | 在 BRAIN 上验证：**冻结工件（假设/表达式/平台证据）上做搜索 + 新鲜上下文的对抗审查者，可重执行实验并否决**；五个用户六种模型后端独立复现出 SPECTACULAR 级 alpha | 我们的预登记 AST + 独立审查渠道与其"冻结工件搜索"同构；**缺"再执行"**——审查者目前只读研究者的报告，不重算关键统计 |
| [RD-Agent(Q)](https://github.com/microsoft/RD-Agent)（微软） | 数据为中心的多智能体量化研究循环，因子-模型协同优化，文献驱动假设 | 借"文献驱动"：把 101 Alphas、Alpha158 等已验证因子作为 mutate 模板注入研究提示（不直接提交，OP 风险） |
| [worldquant-miner](https://github.com/zhutoutoutousan/worldquant-miner) | Ollama 本地 LLM 生成→模拟→提交闭环；**candidate 质量预筛器**（GBDT，115k 公开样本） | 借预筛器思路：在我们自己的 simulations 表上训练 surrogate（特征=角色/算子/设置），预筛低预期假设省模拟额度 |
| [wq-alpha-research](https://github.com/QuantML-Research/wq-alpha-research)（QuantML） | 自进化 SKILL.md：平台 API 细节、模拟失败诊断、IS 指标检查、自相关管理写成领域技能文件 | 把平台操作知识（失败码含义、区域特性、数据集地图）整理进我们的 policy/bindings |

### 算法族（非 LLM，可组合）

| 项目 | 机制 | 对我们的价值 |
|---|---|---|
| [AlphaGen](https://github.com/RL-MLDM/alphagen)（上交，KDD/AAAI） | RL 顺序选算子构造表达式，**以"池协同度"为奖励**而非单 alpha 指标 | 角色选择升级为 bandit/UCB（利用已有 role_probe_summary 档位）；新候选与已提交池低相关时加分 |
| AlphaForge / AutoAlpha / gplearn | 进化搜索 + 性能仲裁者 | DSL 已有 AST 契约，可加程序化枚举器做补充探索（低优先级，LLM 提案质量已够） |
| [101 Formulaic Alphas](https://arxiv.org/pdf/1601.00991)（Kakushadze） | 101 个公开公式 | 只作 mutate 模板；直接提交必撞 PROD_CORRELATION |

### 平台规则（影响成功率/积分的硬约束）

来自 [官方文档](https://platform.worldquantbrain.com)、[金牌顾问经验帖](https://zhuanlan.zhihu.com/p/2011835143673910990)、[Power Pool 规则](https://www.scribd.com/document/1069276969/Power-Pool-Alphas-WorldQuant-BRAIN)：

- **提交门槛**：delay-1 Sharpe > 1.25 / fitness > 1.0；delay-0 Sharpe > 2.0 / fitness > 1.3；turnover 1%–70%；自相关与 PROD 相关 < 0.7（官方 `/alphas/{id}/check` 全 PASS 才可提交——我们已实现）。
- **积分乘数（顾问赛跑）**：地区 EUR ×1.4；delay-0 ×2；主题赛 ×1.5–3.5；SuperAlpha ×3；**连续 14 天提交 +20% 连击**。等级分 F=0.05 … B=1, A=2, S=3。
- **Power Pool**（专项赛资格）：≤8 算子、≤3 数据字段、Sharpe ≥ 1.0、与 Power Pool 池自相关 < 0.5；主题榜最少 5 个打标 alpha。
- **数据集冷热度**：`userCount`/`alphaCount` 低的字段 PROD_CORRELATION 更容易过（catalog.py 已存这两个字段，但未用于选择）。

## 二、差距分析与借鉴清单

我们已有的、社区项目里少见的优势：预登记假设与 AST 冻结、独立审查渠道、真实结果反馈诊断（含 PnL 相关性）、UNKNOWN 对账、预算/授权窗口闸门、多 agent 路由。以下按优先级列缺口。

### P0：直接提高积分与研究产出

1. **地区与 delay 多样性轮换**。当前 100% 模拟集中在 USA / delay-1 / TOP3000（最拥挤组合）。EUR ×1.4、delay-0 ×2 的乘数完全未利用，且冷门组合 PROD_CORRELATION 更易通过。
   落地：settings 多样性进 autopilot-policy（如 1/4 轮 EUR delay-1 TOP2500、1/8 轮 delay-0，注意 delay-0 门槛 Sharpe>2.0 需在审查门同步提高）。数据字段按 region 可用性预检（catalog 已有按 dataset 检索）。
2. **主题打标 + Power Pool 模式**。模拟通过后 `PATCH /alphas/{id}` 打 theme 标签（wqb `patch_properties` 同款 API）；另外加一种"Power Pool 兼容"生成约束：算子数 ≤8、字段数 ≤3，DSL 校验器已具备计数能力。
   落地：brain_submission 接收通过后补打标步骤；policy 增加 powerpool 变体通道。
3. **连击感知调度**。连续每日提交比爆发更值钱（+20%）。autopilot 目标从"周预算用满"微调为"保底每日 1 个可提交候选"：max_cycles_per_day 已可配；需加一条规则——当日无已提交且存在达标候选时优先消耗提交额度。

### P1：提高模拟额度效率

4. **multi-simulation 批量**。顾问权限可 10 个 alpha 一个 POST、3 并发（[官方说明](https://worldquantbrain.com/consultant)）。当前单发+60s 间隔。基础+变体（decay/翻转）天然适合同批。
   落地：brain_jobs 增加批量 payload（数组），响应按 children 逐个入账；保持既有"单并发、仅模拟"授权语义不变（同一授权、同批回测）。
5. **候选预筛器**。在 simulations 表积累的真实样本上训 surrogate（角色、算子数、region/delay、probe 档位 → Sharpe 档位），预期 Sharpe<0.5 的假设不派发模拟。现在样本量（百级）偏小，先做规则版：probe 档位=无信号的角色不再自由探索（paused_clusters 已近似），叠加字段 userCount 加权。

### P2：研究循环质量

6. **再执行式对抗审查**（AgonAlpha 核心可借鉴点）。审查者不只是读报告：给审查任务加"重算"职责——独立用平台证据/PnL 重推关键统计（Sharpe、turnover、相关性），对不上即否决。我们的 review 角色已有独立渠道与 result.json 契约，只需在审查 prompt 中提供 PnL/证据文件并要求重算比对，审查通过标准加"数值一致"。
7. **文献模板注入**。把 101 Alphas/Alpha158 结构作为 mutate 模板加入研究提示（明确标注"模板非指令，需变异且不可直接提交"），与 role 系统正交。
8. **OS 校准**。定期拉取已提交 alpha 的 OS 表现（`/users/self/alphas` OS stage），记录 IS→OS 衰减到账本；feedback 诊断加"该角色/设置历史 OS 衰减"特征，用于提交门校准（某类 IS 高但 OS 崩的角色降权）。这是社区项目普遍缺失、我们有账本优势能做的。
9. **冷门度进角色选择**。catalog 已有 userCount/alphaCount；角色打分加冷门权重（低 userCount 优先），降低 PROD_CORRELATION 风险与撞车率。

### 不建议照搬

- 离线模拟器（偏差未解决，结论不可信）。
- 直接提交 101 Alphas 或公开公式（必撞 PROD_CORRELATION，且无原创性）。
- 绕过平台限速/多账号并发（违反平台条款；我们契约里"不自动充值/不绕过冷却"的原则保持）。

## 三、引用

- wqb: https://pypi.org/project/wqb/ ；multi-simulation/consultant: https://worldquantbrain.com/consultant
- worldquant-miner: https://github.com/zhutoutoutousan/worldquant-miner
- wq-alpha-research: https://github.com/QuantML-Research/wq-alpha-research
- AgonAlpha: https://www.alphaxiv.org（2026-08，BRAIN 上 SPECTACULAR 级复现）
- RD-Agent: https://github.com/microsoft/RD-Agent
- AlphaGen: https://github.com/RL-MLDM/alphagen
- 101 Formulaic Alphas: https://arxiv.org/pdf/1601.00991
- 金牌顾问经验（积分乘数/连击）: https://zhuanlan.zhihu.com/p/2011835143673910990
- Power Pool 规则: https://www.scribd.com/document/1069276969/Power-Pool-Alphas-WorldQuant-BRAIN
- 社区仓库索引: https://github.com/topics/worldquantbrain
