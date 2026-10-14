"""
MoRay MVP v1.0.0 端到端集成验收测试（国庆冲刺 Day4 · 第一阶段收官）
==================================================================
模拟一次完整的单 Agent 长任务：「读取工作区里的一个文件，生成摘要，写入新文件」。
测试脚本扮演"模型大脑"（真实系统里这一环在前端 100_gateway / 外部 MCP 客户端），
其余全部走**生产真实链路**：

  - 应用：server.app.main.app 本体（含 lifespan：init_db + 重启 interrupted 标记）
  - 通道 a：HTTP /api/agent/tool（web 通道）
  - 通道 b：MCP 网关 POST /api/mcp/sse（无 sessionId 的同步 JSON-RPC 便捷模式）
  - 模型口：POST /api/llm/chat（假上游 httpx，验证 Day1 预算计数，零真实网络）
  - 事件流：event_snapshots（Day2）；状态机：agent_sessions（Day3）

协同断言（2.2）：
  a) 通信网关两条通道都能路由到 agent_tools 并执行（含 MCP 白名单拒绝路径）
  b) ≥5 条事件落库，含 input/output/duration_ms，MCP 来源可辨
  c) agent_sessions 完整走完 pending → running → completed
  d) 整个任务 6 次 LLM 调用，远低于上限 30，一次 429 都没有

隔离（2.3）：临时工作区 + 全新临时数据库 + 假上游，不碰生产数据、不碰网络。

运行方式：server\\.venv\\Scripts\\python.exe tests/test_mvp_e2e.py
"""

import json
import os
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---- 测试隔离（导入 config 前设置）：临时工作区 + 全新临时库 + 钉死预算上限 ----
TEST_DIR = PROJECT_ROOT / "test_mvp_e2e_workspace"
TEST_DIR.mkdir(exist_ok=True)
WS = TEST_DIR / "ws"          # Agent 受控工作区
# 工作区必须每次全新：上次运行写下的产物会让「被拒绝的写没发生」这类断言假阳性
shutil.rmtree(WS, ignore_errors=True)
WS.mkdir(exist_ok=True)
TEST_DB = TEST_DIR / "test_mvp_e2e.db"
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ["MORAY_WORKSPACE"] = str(WS)
os.environ["MORAY_DB"] = str(TEST_DB)
os.environ["MORAY_MEMORY_DB"] = str(TEST_DIR / "memory.sqlite3")
os.environ["MAX_LLM_CALLS_PER_SESSION"] = "30"

from fastapi.testclient import TestClient

from server.app import agent_tools, config, crud, llm_proxy
from server.app.main import app  # 生产应用本体（main.py 的 lifespan 原样生效）

SESSION = "mvp-e2e-session"      # 整条链共用的会话归属


# ---------------------------------------------------------------- 假 LLM 上游
class _FakeResponse:
    status_code = 200

    def json(self):
        return {
            "choices": [{"message": {"role": "assistant", "content": "mock-completion"}}],
            "model": "mock-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


class _FakeArgusResponse:
    """[阶段3b] main.lifespan 的调度器/投递器 tick 用的假响应：
    200 + 空列表 → 两个 tick 都空转早退，不打真网络、不留日志。"""
    status_code = 200

    def json(self):
        return {"subscriptions": [], "notifications": []}


class _FakeAsyncClient:
    """覆盖 llm_proxy._non_stream 与 main.py 订阅 loop（[阶段3b]）用到的接口。
    main.lifespan 启动时会用 httpx.AsyncClient(base_url=ARGUS...) 构造订阅客户端，
    被本 mock 全局替换后拿到的是这里——所以 get/post/aclose 必须齐全，
    否则 TestClient 退出（lifespan shutdown）在 aclose 上抛 AttributeError。
    TestClient 用同步 httpx.Client，不受影响。"""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        return _FakeResponse()

    async def get(self, url, params=None, **k):
        return _FakeArgusResponse()

    async def aclose(self):
        return None


# ---------------------------------------------------------------- 任务素材（"大脑"的确定性脚本）
SOURCE_PATH = "notes/source.txt"
SUMMARY_PATH = "notes/summary.txt"
SOURCE_CONTENT = "MoRay Day4 源文件 第一行\n第二行 内容略长\n第三行 结束\n"
# "生成摘要" = 确定性纯函数（真实系统里这步发生在模型里；这里脚本化，结果可复算）
LINES = [l for l in SOURCE_CONTENT.splitlines() if l.strip()]
SUMMARY = "[摘要 %d行] %s" % (len(LINES), LINES[0])


def _events_for(sid: str) -> list[dict]:
    conn = sqlite3.connect(config.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM event_snapshots WHERE session_id = ? ORDER BY id ASC", (sid,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["snapshot"] = json.loads(d.pop("snapshot_json"))
            out.append(d)
        return out
    finally:
        conn.close()


def _purge_default_bucket() -> None:
    """清空 default 桶：agent_sessions 行 + 事件行 + llm_proxy 预算计数内存态。

    为什么需要（Day18）：unittest discover 按字母序导入测试模块，config.SQLITE_PATH
    由字母序首个套件（test_engines）加载 MORAY_DB 时一次性定死，全量跑时所有套件
    共享同一个临时库；前序套件的 default 兜底用例（test_event_stream 的
    test_session_id_from_http_body_and_header 等）会给 default 桶留下会话行。
    不清桶的话，「带 session_id 的链路不应污染 default 桶」断言依赖执行顺序。
    机制详见 docs/day18_test_hygiene.md。
    """
    conn = sqlite3.connect(config.SQLITE_PATH)
    try:
        conn.execute("DELETE FROM agent_sessions WHERE session_id = 'default'")
        conn.execute("DELETE FROM event_snapshots WHERE session_id = 'default'")
        conn.commit()
    finally:
        conn.close()
    llm_proxy._session_calls.pop("default", None)


class TestMvpE2E(unittest.TestCase):
    """一次单 Agent 长任务贯通四组件"""

    @classmethod
    def setUpClass(cls):
        # 假云端配置（防任何真实上游被触达；即便 .env 存在也不依赖其值）
        config.LLM_BASE_URL = "http://mock-upstream.test/v1"
        config.LLM_API_KEY = "sk-mock-e2e"
        config.LLM_MODEL = "deepseek-flash"
        cls._patcher = mock.patch.object(llm_proxy.httpx, "AsyncClient", _FakeAsyncClient)
        cls._patcher.start()
        # with → 触发真实 lifespan：db.init_db() + 重启 interrupted 标记
        cls.client_ctx = TestClient(app)
        cls.client = cls.client_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        cls._patcher.stop()

    def setUp(self):
        # Day18 防顺序污染：全量 discover 时各套件共享同一个临时库，
        # 每个用例前把 default 桶清零，default 断言才不依赖字母序
        _purge_default_bucket()

    # ---------- 三个组件的薄封装（全部打真实路由） ----------
    def _llm_turn(self, note: str):
        """一次 LLM 调用（预算计数 +1），断言未被熔断"""
        r = self.client.post("/api/llm/chat", json={
            "session_id": SESSION, "stream": False,
            "messages": [{"role": "user", "content": note}],
        })
        self.assertEqual(r.status_code, 200, f"LLM 调用被熔断？{r.text}")
        self.assertTrue(r.json().get("ok"))

    def _http_tool(self, name, args, approved=False, decision=""):
        r = self.client.post("/api/agent/tool", json={
            "name": name, "args": dict(args, session_id=SESSION),
            "approved": approved, "decision": decision,
        })
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _mcp_tool(self, name, args, req_id: int):
        """MCP 网关：POST /api/mcp/sse 同步便捷模式（不带 sessionId）"""
        r = self.client.post("/api/mcp/sse", json={
            "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": name, "arguments": dict(args, session_id=SESSION)},
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertNotIn("error", body, f"协议层错误：{body}")   # 拒绝是 result.isError，不是 JSON-RPC error
        return body["result"]

    def _session_row(self):
        r = self.client.get(f"/api/agent/session/{SESSION}/status")
        self.assertEqual(r.status_code, 200)
        return r.json()["data"]

    # ---------- 主场景 ----------
    def test_full_single_agent_long_task(self):
        client = self.client

        # 开局：会话无任何记录 → 状态查询 404（断点恢复语义）
        self.assertEqual(client.get(f"/api/agent/session/{SESSION}/status").status_code, 404)

        # ---- 回合0：准备源文件（模拟工作区已有文件）----
        self._llm_turn("准备源文件")
        j = self._http_tool("write_file", {"path": SOURCE_PATH, "content": SOURCE_CONTENT}, approved=True)
        self.assertTrue(j.get("ok"), j)
        self.assertEqual(self._session_row()["status"], "completed")

        # ---- 回合1：list_directory 找到源文件（HTTP 通道）----
        self._llm_turn("先看工作区")
        j = self._http_tool("list_directory", {"path": "."})
        self.assertTrue(j.get("ok"), j)
        j2 = self._http_tool("list_directory", {"path": "notes"})
        self.assertIn("source.txt", json.dumps(j2["data"], ensure_ascii=False), "源文件应在 notes/ 目录里")

        # ---- 回合2：MCP 网关读源文件（网关通道 a→b 交叉）----
        self._llm_turn("读取源文件")
        result = self._mcp_tool("read_file", {"path": SOURCE_PATH}, req_id=1)
        self.assertFalse(result.get("isError"), result)
        text = result["content"][0]["text"]
        self.assertIn("MoRay Day4 源文件", text)

        # ---- 回合3：MCP 白名单闸门（副作用工具默认禁止 → 网关拒绝但事件归属会话）----
        self._llm_turn("尝试经 MCP 直接写摘要（应被网关拦）")
        blocked = self._mcp_tool("write_file", {"path": SUMMARY_PATH, "content": SUMMARY}, req_id=2)
        self.assertTrue(blocked.get("isError"), "MCP 白名单外副作用工具必须被拒")
        self.assertIn("白名单", blocked["content"][0]["text"])
        self.assertFalse((agent_tools._workspace_root() / SUMMARY_PATH).exists(),
                         "被网关拒绝的写必须真的没发生（对照运行期实际生效工作区）")

        # ---- 回合4：改走 HTTP 通道写摘要 —— 先不带 approved → needs_approval（审批双保险），
        #      等待点会话停 running ----
        self._llm_turn("改走审批流")
        j = self._http_tool("write_file", {"path": SUMMARY_PATH, "content": SUMMARY})
        self.assertTrue(j.get("needsApproval"), j)
        self.assertEqual(self._session_row()["status"], "running", "待审批 = 进行中的等待点")

        # ---- 回合5：批准写入 → 摘要落盘（"大脑"算好的内容，与读到的源文一致）----
        j = self._http_tool("write_file", {"path": SUMMARY_PATH, "content": SUMMARY}, approved=True)
        self.assertTrue(j.get("ok"), j)

        # ---- 回合6：MCP 读回摘要做终验（任务产出确认）----
        self._llm_turn("终验")
        result = self._mcp_tool("read_file", {"path": SUMMARY_PATH}, req_id=3)
        self.assertFalse(result.get("isError"), result)
        self.assertIn(SUMMARY, json.loads(result["content"][0]["text"]).get("content", ""),
                      "摘要文件内容应与'大脑'产出一字不差")

        # ================= 协同断言 =================
        # c) 状态机：任务收尾 → completed，error_msg 干净
        row = self._session_row()
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["error_msg"], "")
        self.assertGreater(row["last_event_id"], 0, "状态应指向会话最新事件")

        # d) 预算熔断：6 次 LLM 调用 < 30，全程未触发 429；未归属调用不落 default 桶
        self.assertEqual(llm_proxy._session_calls[SESSION], 6)
        self.assertLess(llm_proxy._session_calls[SESSION], config.MAX_LLM_CALLS_PER_SESSION)
        self.assertIsNone(crud.get_agent_session("default"), "带 session_id 的链路不应污染 default 桶")

        # a) 事件流：≥5 条完整事件（本场景 7 条）
        events = _events_for(SESSION)
        self.assertGreaterEqual(len(events), 5, f"事件太少：{len(events)}")
        statuses = [e["status"] for e in events]
        self.assertIn("rejected", statuses, "MCP 白名单拒绝应留事件")
        self.assertIn("needs_approval", statuses, "审批等待应留事件")
        self.assertGreaterEqual(statuses.count("ok"), 4)

        mcp_events = [e for e in events if e["snapshot"]["source"] == "mcp"]
        self.assertGreaterEqual(len(mcp_events), 3, "MCP 通道调用应在事件里可辨（source=mcp）")
        for e in events:
            # 每条事件 9 字段载体齐全（Day2 契约）
            self.assertTrue(e["created_at"] and e["tool_name"] and e["status"])
            self.assertGreaterEqual(e["duration_ms"], 0)
            self.assertTrue(json.loads(e["input_json"]), "input 必须非空且可解析")
        for e in [x for x in events if x["status"] == "ok"]:
            self.assertTrue(e["output_json"], "成功事件必须带 output")
            json.loads(e["output_json"])
        for e in [x for x in events if x["status"] in ("rejected", "needs_approval")]:
            self.assertTrue(e["error_msg"], "失败/等待事件必须带 error_msg")

        # 读源文件事件确实携带了源内容 → "输入→处理→输出"在事件流里可追溯
        read_src = [e for e in events if e["status"] == "ok" and "Day4 源文件" in e["output_json"]]
        self.assertTrue(read_src, "read_file 事件应带源文件 output")

        # 时间线回放可达（Day2 重放契约端到端）：审计行 → snapshot_id → 事件
        rows, _total = crud.list_agent_log(limit=50)
        linked = [r for r in rows if r.get("snapshot_id")]
        self.assertGreaterEqual(len(linked), 5, "时间线的重放入口应指向真实事件")

        # 3) 隔离自证：库与工作区都必须落在工程根下 test_ 前缀的测试区，产物真实落盘。
        #    库：config.SQLITE_PATH 在 config 导入期一次性定死，全量 discover 时为字母序
        #    首个套件（test_engines）的临时库；工作区：_workspace_root 每次实时读
        #    MORAY_WORKSPACE，全量先导入全部模块再运行，运行期终值为最后一个导入套件
        #    （test_zcode_adapter_hardened）所设——两者都必然位于工程内 test_*_workspace，
        #    与生产库 server/data/moray.sqlite3、生产默认工作区 D:\MoRayWorkspace 隔离。
        #    单跑时即本套件 TEST_DIR/WS。机制详见 docs/day18_test_hygiene.md。
        self.assertTrue(str(config.SQLITE_PATH).startswith(str(PROJECT_ROOT / "test_")))
        eff_ws = agent_tools._workspace_root(create=False)
        self.assertTrue(str(eff_ws).startswith(str(PROJECT_ROOT / "test_")))
        self.assertTrue((eff_ws / SOURCE_PATH).exists() and (eff_ws / SUMMARY_PATH).exists())

    def test_default_bucket_isolation_between_tests(self):
        """防回归（Day18）：模拟前序套件给 default 桶留下的残留（会话行 + 预算计数），
        setUp 的 _purge_default_bucket 必须能清干净——否则
        test_full_single_agent_long_task 的 default 断言会依赖执行顺序（Day17 发现的
        全量 discover 顺序污染，写入者 test_event_stream default 兜底用例）。"""
        crud.transition_agent_session("default", "running", error_msg="")
        llm_proxy._session_calls["default"] = 99
        self.assertIsNotNone(crud.get_agent_session("default"), "前置：污染必须先种上")
        _purge_default_bucket()
        self.assertIsNone(crud.get_agent_session("default"), "清桶后 agent_sessions 不得残留 default 行")
        self.assertNotIn("default", llm_proxy._session_calls, "清桶后预算计数不得残留 default 键")

    def test_startup_recovery_hook(self):
        """main.app lifespan 的 interrupted 标记已被真实执行：直接库内验证语义。
        （跑在长任务用例之后：手工种一条 running 会话，调恢复函数 → interrupted）"""
        crud.transition_agent_session("mvp-e2e-stale", "running", error_msg="")
        n = crud.mark_stale_sessions_interrupted()
        self.assertGreaterEqual(n, 1)
        row = crud.get_agent_session("mvp-e2e-stale")
        self.assertEqual(row["status"], "interrupted")


def run_tests():
    print("=" * 62)
    print("MoRay MVP v1.0.0 端到端集成验收（Day4）")
    print("=" * 62)
    print(f"测试库: {TEST_DB}")
    print(f"工作区: {WS}")
    print("=" * 62)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestMvpE2E))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print()
    print("=" * 62)
    if result.wasSuccessful():
        print(f"✅ 全部 {result.testsRun} 项通过：网关×事件流×状态机×预算熔断 端到端协同成立。")
    else:
        print(f"❌ 失败 {len(result.failures)} / 错误 {len(result.errors)}（共 {result.testsRun}）")
        for test, tb in result.failures + result.errors:
            print(f"  - {test}")
    print("=" * 62)
    return result.wasSuccessful()


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
