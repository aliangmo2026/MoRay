"""非零退出码传递测试脚本。

用于验证调用方（shell / subprocess / CI）能否正确捕获退出码 1。
"""

import sys

print("assert_exit: 故意以退出码 1 结束，用于测试非零退出码传递")
sys.exit(1)
