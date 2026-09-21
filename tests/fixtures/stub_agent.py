"""测试用 stub "agent" 二进制：验证 wrapper 契约，不调用任何真实模型。

用法: python3 stub_agent.py --mode ok|sleep|crash|badjson|noartifact --artifact PATH
"""
import json
import os
import sys
import time


def main() -> int:
    mode, artifact = "ok", None
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
        if a == "--artifact" and i + 1 < len(args):
            artifact = args[i + 1]
    if mode == "sleep":
        time.sleep(60)
        return 0
    if mode == "crash":
        os._exit(3)
    if artifact:
        os.makedirs(os.path.dirname(os.path.abspath(artifact)), exist_ok=True)
        if mode == "badjson":
            with open(artifact, "w") as f:
                f.write("not json {")
        elif mode != "noartifact":
            with open(artifact, "w") as f:
                json.dump({"decision": "NO_ACTION", "stub": True}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
