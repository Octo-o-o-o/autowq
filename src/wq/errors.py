"""退出码与异常。所有命令的真实结果都映射到这些码，不允许假成功。"""

OK = 0
ERR = 1            # 通用错误（I/O、DB、内部异常）
USAGE = 2          # 用法错误（argparse 约定）
BLOCKED = 3        # 策略/授权/预算/配额阻断
NOT_IMPLEMENTED = 4  # 功能未接通（如真实 BRAIN adapter）
INVALID = 5        # 数据契约/校验失败
PAUSED = 6         # 已暂停
DUPLICATE = 7      # 业务去重命中（幂等场景下不算错误）


class WqExit(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


class ContractError(Exception):
    """数据契约校验失败，携带全部错误列表。"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class AdapterError(Exception):
    """adapter 层的可归因错误。kind 决定上层动作（暂停/退避/对账）。"""

    AUTH = "auth"                    # 401/403：身份或权限问题 → 暂停，交人工
    RATE_LIMIT = "rate_limit"        # 429：遵守 Retry-After 与总等待上限
    QUOTA = "quota"                  # 配额不足：阻断，不换账号
    UNKNOWN_REMOTE = "unknown_remote"  # 超时等：远端可能已接受 → 先对账，不重发
    NETWORK = "network"
    POLICY = "policy"                # 本地策略拒绝（如 manual 模式请求真实模拟）
    NOT_IMPLEMENTED = "not_implemented"

    def __init__(self, kind: str, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.kind = kind
        self.retry_after = retry_after
