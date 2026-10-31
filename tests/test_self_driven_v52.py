"""[v5.2] 判据覆盖检查 + 重试卡三件套 单测。

两件事都是 2026-10-31 实测出来的真伤：
1. 卡 4004b7bf 的 claim 写 "contains exactly Hello 世界 with 10 bytes"，而
   verify_cmd 只比较内容相等，从未校验字节数 —— 判成"预测成立"时，"10 bytes"
   这句既没人验、本身还是错的（实际 12 字节）。→ verify_cmd_coverage 打标。
2. 失败重试链路走的是 v2 的 _parse_goal_payload（只有 goal+engine），于是
   "失败重试"产出的卡天生没有 verify_cmd，审批一次也判不了真假。→ 改走 v3
   三件套 + 硬缺陷拒发卡。

全部离线：LLM 用 monkeypatch 顶掉，状态文件用 tmp_path，绝不碰真实队列/配额。
跑法：D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests\\test_self_driven_v52.py -q
"""
import asyncio
import json
import sys

import pytest

sys.path.insert(0, r"D:\MoRay")

from server.app import self_driven as sd  # noqa: E402


# ---------------- 1. 判据覆盖 ----------------

def test_coverage_flags_real_byte_clause():
    """真实战例：断言 10 bytes，判据只查内容 → 必须报未覆盖。"""
    claim = "D:\\MoRay\\utf_test.txt contains exactly Hello 世界 string with 10 bytes"
    cmd = (r'python -c "import io,os,sys;p=r\'D:\MoRay\utf_test.txt\';'
           r"sys.exit(0 if os.path.exists(p) and io.open(p,encoding='utf-8')"
           r".read()=='Hello 世界' else 1)\"")
    assert sd.verify_cmd_coverage(claim, cmd) == ["10 bytes"]


def test_coverage_quiet_when_number_checked():
    """判据里出现同一个数字（真的校验了字节数）→ 不报。"""
    claim = "文件正好 12 字节"
    cmd = (r'python -c "import os;sys.exit(0 if os.path.getsize('
           r'r\'D:\MoRay\utf_test.txt\')==12 else 1)"')
    assert sd.verify_cmd_coverage(claim, cmd) == []


@pytest.mark.parametrize("claim,cmd", [
    ("", r"dir D:\MoRay"), (r"断言 5 行", ""), (None, None), ("没有数字条款", "dir ."),
])
def test_coverage_empty_inputs(claim, cmd):
    assert sd.verify_cmd_coverage(claim, cmd) == []


def test_coverage_never_raises():
    for weird in (0, [], {"a": 1}, "\x00", "10 bytes"):
        assert isinstance(sd.verify_cmd_coverage(weird, weird), list)


def test_coverage_multiple_clauses_dedup():
    claim = "输出 3 个文件，每个 2 行，共 3 个文件"
    assert sd.verify_cmd_coverage(claim, "dir .") == ["3 个", "2 行"]


# ---------------- 2. 主链发卡带覆盖标记 ----------------

@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sd, "STATE_FILE", tmp_path / "self_driven_state.json")
    monkeypatch.setattr(sd, "_goal_inflight", False)
    monkeypatch.setattr(sd, "_fetch_mental_state",
                        lambda: {"last_result": "success", "engine": "zcode"})
    return tmp_path / "self_driven_state.json"


def _payload(goal, verify, claim="断言", pred="预期数值 1", engine="zcode"):
    return json.dumps({"goal": goal, "claim": claim, "prediction": pred,
                       "verify_cmd": verify, "target_engine": engine},
                      ensure_ascii=False)


def test_generate_card_carries_coverage_uncovered(tmp_state, monkeypatch):
    async def fake_llm(messages):
        return _payload("写文件并核对字节数",
                        r"dir D:\MoRay\server\app\self_driven.py",
                        claim="生成 D:\\MoRay\\out.txt 共 10 bytes"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    card = asyncio.run(sd.generate_goal())
    assert card is not None
    assert card["coverage_uncovered"] == ["10 bytes"], "覆盖不全必须留痕在卡上"


def test_generate_card_clean_coverage(tmp_state, monkeypatch):
    async def fake_llm(messages):
        return _payload("核对文件字节数",
                        r"python -c \"import os,sys;sys.exit(0 if os.path.getsize("
                        r"r'D:\MoRay\out.txt')==10 else 1)\"",
                        claim="生成 D:\\MoRay\\out.txt 共 10 bytes"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    card = asyncio.run(sd.generate_goal())
    assert card is not None
    assert card["coverage_uncovered"] == []


# ---------------- 3. 重试卡必须带三件套 ----------------

def _seed_failed_card(engine_task_id="e1", retry_count=0):
    sd._save_state({"daily": {"date": sd._today(), "count": 1},
                    "queue": [{"task_id": "parent-1", "goal": "原目标",
                               "target_engine": "zcode",
                               "engine_task_id": engine_task_id,
                               "status": "failed",
                               "retry_count": retry_count}]})


def test_retry_card_carries_three_pack(tmp_state, monkeypatch):
    _seed_failed_card()

    async def fake_llm(messages):
        # prompt 里必须已经要求三件套（否则模型永远只吐 goal+engine）
        assert "verify_cmd" in messages[0]["content"]
        assert "claim" in messages[0]["content"]
        return _payload("改用 laya 重写并核对字节数",
                        r"dir D:\MoRay\server\app\self_driven.py",
                        claim="文件正好 12 bytes", pred="存在且 12 字节",
                        engine="laya"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    card = asyncio.run(sd.retry_handler("e1", "boom"))
    assert card is not None
    assert card["status"] == "pending_approval"
    assert card["retry_of"] == "parent-1"
    assert card["retry_count"] == 1
    # 三件套到位：这张重试卡是真能被现实判真假的
    assert card["verify_cmd"].startswith("dir D:")
    assert card["claim"] == "文件正好 12 bytes"
    assert card["prediction"] == "存在且 12 字节"
    assert card["target_engine"] == "laya"
    assert "coverage_uncovered" in card


def test_retry_card_refused_when_verify_unrunnable(tmp_state, monkeypatch):
    """重试链的硬缺陷同样不发卡：不为一张注定不可判的卡消耗审批。"""
    _seed_failed_card()

    async def fake_llm(messages):
        return _payload("继续用 /tmp", "/tmp/bad.py"), ""

    monkeypatch.setattr(sd, "_llm_goal", fake_llm)
    assert asyncio.run(sd.retry_handler("e1", "boom")) is None
    state = json.loads(tmp_state.read_text(encoding="utf-8"))
    assert len(state.get("queue") or []) == 1, "只应留父卡，不许多出重试卡"


def test_retry_unknown_engine_task_is_silent(tmp_state, monkeypatch):
    _seed_failed_card("e1")
    async def boom(messages):
        raise AssertionError("不该调用 LLM")
    monkeypatch.setattr(sd, "_llm_goal", boom)
    assert asyncio.run(sd.retry_handler("nope", "boom")) is None
