# -*- coding: utf-8 -*-
"""[Day24b] 构建后验证（只读）：构建产物 marker、api 语法、diff 行数统计。
输出刻意只用 ASCII + 数字（显示层会把英文词改写成拼音，数字/布尔可抗改写）。"""
import ast
import io
import os

R = r"D:\MoRay"
HTML = os.path.join(R, "moray-workbench.html")
API = os.path.join(R, "server", "app", "api.py")
ASM = os.path.join(R, "assemble.py")
P89 = os.path.join(R, "parts", "89_zcode_stream.js")
C70 = os.path.join(R, "parts", "70_models_settings_boot.js")
BAK = os.path.join(R, "backup", "day24b_20260928")


def read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def lines(path):
    return read(path).count("\n") + 1


# chk01: api.py 语法（ast.parse，任务书红线指定的检查形式）
try:
    ast.parse(read(API))
    print("chk01_ast_api_ok", True)
except SyntaxError as e:
    print("chk01_ast_api_ok", False, e.lineno)

html = read(HTML)
checks = [
    ("chk02_html_panel_id", "morayZcodeStreamPanel" in html),
    ("chk03_html_title", "ZCode 任务监控" in html),
    ("chk04_html_stream_url", "/api/engines/zcode/stream/" in html),
    ("chk05_html_tasks_url", "/api/engines/zcode/tasks" in html),
    ("chk06_html_mount_step", "runStep('zcode-stream'" in html),
    ("chk07_html_launcher_btn", "morayZcodeStreamBtn" in html),
    ("chk08_html_done_sentinel", "[DONE]" in html),
    ("chk08b_html_whitelist", "turn.completed" in html and "checkpoint.created" in html),
    ("chk09_asm_part89", "'parts/89_zcode_stream.js'" in read(ASM)),
    ("chk10_api_routes", "zcode_stream_task_create" in read(API) and "zcode_stream_events" in read(API)),
    ("chk11_api_whitelist", "_ZCODE_EVENT_WHITELIST" in read(API)),
    ("chk12_c70_mount", "MorayZCodeStream" in read(C70)),
]
for name, ok in checks:
    print(name, bool(ok))

# 行数统计（报告用）
print("== line counts ==")
print("api.py.now", lines(API))
print("api.py.orig", lines(os.path.join(BAK, "api.py.orig")))
print("part89", lines(P89))
print("70.now", lines(C70))
print("70.orig", lines(os.path.join(BAK, "70_models_settings_boot.js.orig")))
print("assemble.now", lines(ASM))
print("assemble.orig", lines(os.path.join(BAK, "assemble.py.orig")))
