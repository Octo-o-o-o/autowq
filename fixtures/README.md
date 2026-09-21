# fixtures

全部为 **synthetic** 测试数据（`"synthetic": true`）。仅用于验证契约与管线，
不得导入真实研究/收益报告；`wq report` 默认排除 synthetic 记录。

- `synthetic-card.json` — 抽象研究卡示例（无真实表达式）
- `synthetic-result-pass.json` — 模拟结果导入示例（含累计 PnL 序列）
- `../tests/fixtures/stub_agent.py` — agent wrapper 契约测试用 stub 二进制
