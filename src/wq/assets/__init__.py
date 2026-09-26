"""包内运行时资产：config 模板与独立辅助脚本，wheel 安装后可用。"""
from importlib.resources import files


def path(name):
    return files(__name__) / name
