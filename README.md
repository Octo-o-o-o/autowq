# autowq

本地运行的量化研究工作流：模型提出假设 → 不同渠道审查 → 本地证据门禁 → BRAIN 单次回测 → 查询结果 → 入账归档 → 下一轮。

Python 3.11+，运行时只依赖标准库。模型 CLI、账号及平台权限由部署者自行配置。当前是私有研究项目，无自动 Alpha 提交功能，不承诺策略有效或收入。

## 开始使用

```sh
python3 scripts/setup_local.py
./wq doctor --fix-private
PYTHONPATH=src python3 -m unittest discover -s tests
./wq tasks
```

`setup_local.py` 只生成**全部关闭**的本机配置、macOS 沙箱入口和 launchd 文件，拒绝覆盖已有部署，不登录、不调用模型、不安装定时器。已有运行实例无需执行此初始化。

- [运行、配置、重试与恢复](docs/operations.md)
- [架构、数据边界和验收层次](docs/architecture.md)
- [本次检查与交付记录](docs/release-review.md)
- [第三方与许可说明](NOTICE.md)

## 常用命令

```sh
./wq autopilot status
./wq tasks
./wq preset list
./wq preset use core-only
./wq provider disable cursor
./wq provider enable cursor
./wq autopilot stop
./wq pause --reason "暂停所有任务"
```

预设在下一项任务首次领取时冻结；在途任务及重试保留原预设。每个渠道首次失败后最多重试三次；只有明确额度或服务容量故障才切换备用渠道。UNKNOWN 请求需核实，禁止自动重发。

默认持续研究节奏为轮次结束后间隔一小时、UTC 每日最多四轮、每周最多二十四次模拟，并始终单并发。实际部署可调整，但授权期限、Provider 额度和平台 Retry-After 仍然生效。

## 仓库内容与本地状态

本仓库包含完整程序、测试、合成 fixtures、关闭的配置模板与部署生成器。账号、cookie、实际模型输出、真实 Alpha、运行数据库、授权文件和个人研究档案只保留在部署机器上，不进入 Git。新 clone 不继承旧机器的任何授权。

只有现金账本中的实际到账记录才表示收入；平台检查通过、提交接受、最终有效和报酬资格分别记录。离线测试不证明真实服务权限，短期闭环不证明长期无人值守或盈利。
