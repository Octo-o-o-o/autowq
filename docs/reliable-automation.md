# 持续运行、研究边界与恢复

0.2.22 本地候选包含多泳道可靠性修复；公开下载仍为 0.2.21，尚未发布此候选。

## 什么会继续，什么会等待

后台调度每分钟检查队列。`concurrent_lanes` 是同时开放轮次的上限，不保证所有泳道始终有任务：同一模型渠道的调用保持串行，BRAIN 模拟和提交也保持串行。冷却、日/周预算、总轮数、未知结果、证据和授权分别约束派发。一个泳道失败不会清掉其他泳道的已知进度。

双环模式保留两种不同的研究：

- Alpha 研究产生假设，异渠道审查后进行有限模拟；真实结果、失败和晚到资料进入账本。注册 campaign 的资源单独计量，不混入普通 baseline/learning 对照。
- 项目自身研究目前执行已登记的证据检查缓存对照、运行测量、维护和问题建议。缓存没有收益证据时继续使用完整检查；不会把一次性能对照、模型建议或模拟 Sharpe 宣称为持续收益，也不会自行部署模型生成的代码。

同一冻结输入默认最多预约两次无信息发现，可通过运行额度设置调整。有效的真实模拟回执释放对应发现预约，并把已验证的新观测纳入后续输入身份；旧失败记录仍保留。升级时没有有效回执便沿用旧输入身份，不凭空补充发现位；没有新资料时等待是预期行为。模型启动累计上限默认 64、取证读取累计上限默认 24，均可在托盘「运行额度与权限」中调整；已用计数沿原双环 root 保留，失败与重启不会退还额度。调高实验轮数不等于调高这两个上限。授权到期或额度耗尽后需要明确的新授权，程序不能通过换 root、重启或迁移自行续额。

## 定位“只运行了一轮”

运行 `wq research-framework report`。检查 `progress.runtime_source`、`progress.experiment`、`progress.dual_resources` 和 `progress.maintenance_error`；同时查看 `wq autopilot status` 的 `last_tick_at`、开放泳道和 `message`。

| 状态 | 含义与后续动作 |
|---|---|
| 调度时间持续刷新，实验 `baseline_superseded` | 代码或研究配置变了。核对最终版本，再精确迁移实验。单纯等待不会恢复。 |
| `waiting_changed_observation` | 同一输入的无信息发现位已占满。等待在途真实结果或补充登记材料，不重放旧任务。 |
| `waiting_unreadable_or_changed_consumed_receipt` | 已消费的真实回执缺失或被修改。恢复原回执；不能靠删除文件、修改描述或改写指标获得新发现位。 |
| `dual_model_budget_exhausted` | 累计模型启动额度耗尽；不会因为换 epoch 获得新额度。 |
| 框架检查失败 | 修复所示配置、材料或摘要错误；现有轮次仍允许对账。 |
| UNKNOWN | 先对账已发出的任务，确认结果；不能把未确认结果当成失败重发。 |
| 日/周上限或平台冷却 | 在原授权仍有效时，到期自动再检查。 |
| `owner_stop` | 保留人工停止，基线迁移不能解除。 |

## 审核后迁移代码／配置版本

先让所有开放轮次结束并完成 UNKNOWN 对账。将最终候选部署到固定目录；不要一边运行实验一边编辑它加载的源码。可以保留原工作区的数据、配置和日志，让 runner 的 `PYTHONPATH` 指向不可变版本快照。菜单使用同一个 `WQ_ENGINE_SOURCE`；源码目录不可用时应恢复部署。

```sh
wq research-framework prepare-epoch --file epoch-transition.json --owner local-owner --reason 'Reviewed local code/configuration update'
# 核对文件中的来源实验、目标基线和有效期。
wq research-framework approve-epoch --file epoch-transition.json
wq research-framework report
```

第一步只生成精确迁移文件，不续期、不启动模型。第二步持有 runner 锁、复核当前源码/配置与文件一致后迁移，继承原 root 的累计资源；重复执行不会多建 epoch。文件生成后又改变源码或配置，需要重新生成。存在开放轮次、人工停止、UNKNOWN、授权到期或预算不足时，操作明确失败并保留原实验。

## 本地验收范围

`PYTHONPATH=src:tests python3 -m unittest test_reliability -v` 覆盖预算竞争、发现位、campaign 资源隔离、错误展示、参考选择、字段证据一致性、精确迁移以及三轮双泳道研究→审查→模拟→重启恢复。它使用隔离数据库和模拟的模型/BRAIN 响应，没有调用真实平台。

全套工程测试与实际安装包冒烟应另行运行。模拟测试通过不代表真实渠道服务可用、平台已接收新模拟、Alpha 收益改善或公开版本已发布；这些事实分别保留证据。

2026-10-03 的第二轮复检在 macOS 上运行全部 894 项测试，全部通过、无跳过；包含真实 Docker 隔离与超时回收测试。fresh reviewer 对 7 个相关模块运行 155 项测试，并独立验证回执删除／修改不会续额、新真实回执仍能唤醒，以及累计 64 次模型上限。此证据覆盖工程行为，不构成金融优越性结论。
