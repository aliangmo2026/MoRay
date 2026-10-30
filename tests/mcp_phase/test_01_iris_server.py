# -*- coding: utf-8 -*-
"""p_mcp·任务1 Iris 侧单测：规则链 / engine 三能力（假网关）/ PDF / 超时映射 / MCP 信封分发。
运行：server\\.venv\\Scripts\\python.exe -X utf8 tests/mcp_phase/test_01_iris_server.py
（零真实模型调用；对死端口 127.0.0.1:9399 仅做即时拒连计时。）"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, r"D:\MoRay")
sys.path.insert(0, r"D:\Iris\server")   # 放最前：让 mcp_server 的 `import engine` 命中
                                        # D:\Iris\server\engine.py 而不是 D:\MoRay\engine\ 包

import engine as eng  # noqa: E402  （sys.path[0]=D:\Iris\server → 解析到引擎模块）
import rules  # noqa: E402
import pdf_text  # noqa: E402

TRACEBACK = ('Traceback (most recent call last):\n'
             '  File "D:\\proj\\app.py", line 12, in <module>\n'
             '    import foo\n'
             "ModuleNotFoundError: No module named 'foo'")


def build_pdf(flate: bool = False) -> bytes:
    def stream(lines):
        s = ("BT /F1 12 Tf 72 720 Td " + " ".join(f"({l}) Tj 0 -16 Td" for l in lines)
             + " ET").encode()
        return s
    s1, s2 = stream(["Iris Unit Doc", "ERROR: unit failure"]), stream(["Chapter 9 Wrap"])
    objs = [b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
            b"2 0 obj << /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >> endobj\n",
            b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n",
            b"4 0 obj << /Length " + str(len(s1)).encode() + b" >> stream\n" + s1 + b"\nendstream endobj\n",
            b"5 0 obj << /Type /Page /Parent 2 0 R /Contents 6 0 R >> endobj\n"]
    body = zlib.compress(s2) if flate else s2
    objs.append(b"6 0 obj << /Length " + str(len(body)).encode()
                + (b" /Filter /FlateDecode" if flate else b"")
                + b" >> stream\n" + body + b"\nendstream endobj\n")
    return b"%PDF-1.4\n" + b"".join(objs)


class TestRules(unittest.TestCase):
    def test_features_and_severity(self):
        f = rules.extract_features(TRACEBACK)
        self.assertEqual(f["type"], "ModuleNotFoundError")
        self.assertIn("12", f["lines"])
        self.assertEqual(f["lang"], "python")
        self.assertEqual(rules.classify_error(TRACEBACK)["level"], "fatal")

    def test_parse_result(self):
        r = rules.parse_result("【错误根因】缺包\n【修复方案】装上\n【修复代码】\n"
                               "```python\nimport foo\n```\n")
        self.assertEqual((r["root"], r["lang"], r["code"]), ("缺包", "python", "import foo"))

    def test_diag_prompt_hint_injection(self):
        f = rules.extract_features(TRACEBACK)
        p = rules.build_diag_prompt(TRACEBACK, f, hint_text="1. 虚拟环境缺依赖（历史 3 次，成功率 100%）")
        self.assertIn("历史高频根因（优先假设", p)
        self.assertIn("虚拟环境缺依赖", p)
        self.assertNotIn("历史高频根因", rules.build_diag_prompt(TRACEBACK, f))


class FakeEngine(eng.IrisEngine):
    """假网关：按 prompt 前缀回放预置应答，记录全部 prompt（零网络）。"""
    def __init__(self, ocr_text=TRACEBACK, diag_text="【错误根因】缺依赖\n【修复方案】pip install foo\n"
                                                     "【修复代码】\n```python\npass\n```"):
        super().__init__(gateway_url="http://fake")
        self.ocr_text, self.diag_text = ocr_text, diag_text
        self.prompts = []
        self.ocr_calls = 0

    async def _gateway_chat(self, messages, temperature=0.0, force_cloud=False):
        self.prompts.append((json.dumps(messages, ensure_ascii=False), force_cloud))
        first = str(messages[0].get("content")) + str(messages[-1].get("content"))
        if "OCR 引擎" in first:
            self.ocr_calls += 1
            return self.ocr_text, {"total_tokens": 10}
        return self.diag_text, {"total_tokens": 20}


class TestEngineOffline(unittest.IsolatedAsyncioTestCase):
    async def test_ocr_only(self):
        e = FakeEngine()
        out = await e.ocr_only(image_base64="iVBORw0KGgo=")
        self.assertIn("ModuleNotFoundError", out["text"])

    async def test_ocr_retry_then_failed(self):
        e = FakeEngine(ocr_text="")
        with self.assertRaises(eng.IrisError) as cm:
            await e.ocr_only(image_base64="iVBORw0KGgo=")
        self.assertEqual(cm.exception.code, "IRIS_PARSE_FAILED")
        self.assertEqual(e.ocr_calls, 2)  # 重试 1 次 = 共 2 次

    async def test_analyze_error_structure(self):
        e = FakeEngine()
        out = await e.analyze_error(image_base64="iVBORw0KGgo=", hint_text="1. 缺依赖（历史 5 次）")
        for k in ("error_type", "error_content", "root_cause_candidates", "file_type",
                  "file_path", "line_number", "severity"):
            self.assertIn(k, out)
        self.assertEqual(out["error_type"], "ModuleNotFoundError")
        self.assertEqual(out["severity"], "fatal")
        self.assertEqual(str(out["line_number"]), "12")
        self.assertIn("app.py", out["file_path"])
        self.assertGreaterEqual(len(out["root_cause_candidates"]), 2)  # llm + rules/experience
        diag_calls = [c for c in e.prompts if c[1]]
        ocr_calls = [c for c in e.prompts if not c[1]]
        self.assertEqual(len(diag_calls), 1)   # 诊断走 force_cloud
        self.assertEqual(len(ocr_calls), 1)    # OCR 不旁路（本机视觉走本地路由）
        self.assertIn("缺依赖", diag_calls[0][0])  # hint 进了诊断 prompt
        self.assertNotIn("缺依赖", ocr_calls[0][0])

    async def test_invalid_inputs(self):
        e = FakeEngine()
        for kwargs in ({}, {"image_path": r"D:\nope.png"},
                       {"image_path": r"D:\a.png", "image_base64": "aGk="}):
            with self.assertRaises(eng.IrisError) as cm:
                await e.ocr_only(**kwargs)
            self.assertEqual(cm.exception.code, "IRIS_INVALID_INPUT")

    async def test_real_gateway_timeout_mapping(self):
        e = eng.IrisEngine(gateway_url="http://127.0.0.1:9399", timeout=3)
        with self.assertRaises(eng.IrisError) as cm:
            await e._gateway_chat([{"role": "user", "content": "x"}])
        self.assertEqual(cm.exception.code, "IRIS_TIMEOUT")

    async def test_read_pdf(self):
        tmp = Path(tempfile.mkdtemp(prefix="mcp_pdf_"))
        p_plain = tmp / "a.pdf"; p_plain.write_bytes(build_pdf())
        p_flate = tmp / "b.pdf"; p_flate.write_bytes(build_pdf(flate=True))
        e = eng.IrisEngine()
        r1 = await e.read_pdf(str(p_plain))
        r2 = await e.read_pdf(str(p_flate), pages="2")
        self.assertIn("unit failure", r1["full_text"])
        self.assertIn("Wrap", r2["full_text"])          # FlateDecode + 选页
        self.assertEqual(r2["extracted_pages"], 1)
        with self.assertRaises(eng.IrisError) as cm:
            await e.read_pdf(__file__)                  # 非 PDF
        self.assertEqual(cm.exception.code, "IRIS_INVALID_INPUT")
        empty = tmp / "e.pdf"; empty.write_bytes(build_pdf().replace(b"Iris Unit Doc", b"x").replace(
            b"ERROR: unit failure", b"ok"))
        r3 = await e.read_pdf(str(empty))               # 有文本层 → 正常
        self.assertEqual(r3["code"] if "code" in r3 else "OK", "OK")


class TestMcpEnvelope(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_unknown_tool(self):
        import mcp_server as ms
        env = await ms._dispatch_tool("nope", {})
        self.assertEqual(env["code"], "IRIS_INVALID_INPUT")

    async def test_dispatch_pdf_envelope_ok(self):
        import mcp_server as ms
        tmp = Path(tempfile.mkdtemp(prefix="mcp_env_")) / "d.pdf"
        tmp.write_bytes(build_pdf())
        env = await ms._dispatch_tool("iris_read_pdf", {"pdf_path": str(tmp)})
        self.assertEqual(env["code"], "OK")
        self.assertIn("Iris Unit Doc", env["data"]["full_text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
