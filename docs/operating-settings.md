# 托盘运行设置

0.2.24 起在「设置…」独立原生窗口编辑。左侧按通用、研究、调度、模型、模拟、调用、授权分组，可搜索并展开高级参数；支持批量保存、撤销未保存编辑和取消待应用修改。显示当前值、累计已用、待应用值和模式限制。数值／日期输入由同一控制端校验，macOS 和 Python 托盘共用设置目录。日期必须带时区。

运行期间保存先写入持久待应用请求；调度器停止创建新工作，排空现有任务与轮次后原子写入配置，迁移实验 epoch 并继承原累计用量。配置被外部修改、引擎更换、迁移失败时显示错误，不声称已生效。可取消尚未应用的请求。进程崩溃后的应用状态由调度器恢复。

| 设置组／键 | 实际执行端与适用范围 |
| --- | --- |
| `research_dual_loop.max_model_starts` | `research_meta.reserve_model_start` 的累计启动预约；失败不退额 |
| `research_dual_loop.max_evidence_reads`, `max_reads_per_source` | `research_evidence.reserve_read`；每来源还受登记合同的 max_attempts 限制 |
| `research_dual_loop.max_empty_discoveries` | `research_meta.discovery_permission`；同一冻结输入无信息发现预约 |
| `research_dual_loop.max_evidence_tasks_per_day` | `research_evidence.daily_capacity` 的当日取证／核验闸门 |
| `research_framework.evidence_tasks_per_day` | `research_framework` 每日资料采集闸门；0 禁止新增 |
| `research_learning.refresh_per_day`, `maintenance_interval_s` | 历史反馈刷新与维护；仅在对应功能启用时执行 |
| `research_learning.experiment_cycles`, `requests_per_arm` | 新实验的默认值；已有实验使用单独的「实验累计轮数／每组请求上限」 |
| 实验累计轮数／每组请求上限 | `research_lifecycle` 修改同一 lineage 合同并保留已用；增加轮数会提高请求下限 |
| `history_research.every_cycles`, `min_interval_hours` | 历史复盘调度；双环模式接管时明确显示不适用 |
| `limits.configs_per_family_max` | 导入器限制每个假设族的配置数量 |
| `autopilot.interval_s`, `concurrent_lanes`, `max_cycles_per_day`, `max_cycles_total`, `error_cooldown_s` | 下一轮时刻、泳道创建、UTC 日／累计轮次和失败冷却闸门 |
| `autopilot.max_simulations_per_week`, `limits.sims_per_week` | 自动研究模拟闸门；后者还覆盖通用模拟任务派发 |
| `brain_api.max_posts_per_24h`, `min_post_interval_s`, `authorized_until` | 实际 BRAIN 模拟 POST 的滚动窗口、间隔、授权闸门 |
| `brain_submission.max_posts_per_24h`, `authorized_until` | 实际提交 POST 闸门；额度不替代逐项验收与提交动作 |
| `limits.max_agent_parallel`, `supervisor_max_s`, `rate_limit_max_wait_s` | 模型工作池、批次停止领新任务、通用限流等待 |
| `limits.max_repair_attempts_per_incident` | 模型调用前的同事件修复次数闸门 |
| `routing.max_retries`, `retry_delay_s`, `provider_timeouts.*`, `quota_probe_s` | 新任务冻结路由快照中的尝试次数、延迟、渠道超时，以及额度复查间隔 |
| `limits.*_calls_per_week`, `devin_tickets_per_week` | 渠道周上限；0 无周上限；调试授权豁免时明确标注 |
| `budgets.*.remaining` | 调用预算基数减原计数起点以来已用；空值为未知；不重置起点 |
| `limits.model_spend_cap_usd` | 已知美元费用上限，保留原计数起点；未知费用仍单独显示 |
| `research_dual_loop.valid_until`, `routing.authorized_until` | 双环／路由独立授权期限，不自动续其它授权 |

相同渠道仍最多一个调用；BRAIN 模拟与提交保持串行，这是程序约束。`limits.families_max`、`limits.sim_concurrency` 没有执行读取者，已从默认配置与模板移除，不提供无效控件。科学策略、证据合同、root 身份及其冻结期限不作为普通额度编辑；对应状态在研究进展中显示。

设置能力本身不增加任何额度。模板默认仍是累计模型启动 64；当前本机按用户要求设为 256。调高额度不会刷新已有用量，不会自动延长授权。


## 交互与一致性

macOS 菜单仅保留状态、开始／停止、记录入口和设置。研究、历史、已提交与备用记录合并到可搜索的列表／详情窗口。设置使用系统字体、语义色、原生开关、菜单、数值输入和日期选择器，适配系统明暗模式；⌘, 打开设置，⌘S 保存。关闭带草稿的设置窗口会提示，切换分类和搜索保留草稿。

运行设置、模型路由、自定义渠道和泳道选项共同使用持久待应用请求。批量参数全部通过校验后才保存；基于旧版本打开的编辑拒绝覆盖后来修改。模型预设显示长期设置与临时生效状态的区别。生效后结束旧调度批次，让新的并行度与批次时限真正被重新读取。

语言、通知和启动偏好可立即生效，不进入科学基线；与已有运行草稿合并时保留其内容。停止研究意味着不新建轮次并等待现有轮次完成。自动提交开关如实反映实际执行行为，仍受授权期限、提交额度与 Alpha 验收控制。花费上限保留原累计起点，首次设置也不清零既有费用。

Python 托盘使用同一元数据和保存协议，提供原生 ttk 设置窗口；日期采用带时区文本输入。macOS 的记录阅读器与系统日期控件属于原生客户端实现，不能以此宣称 Windows 界面已验收。
