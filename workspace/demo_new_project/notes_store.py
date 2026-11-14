# -*- coding: utf-8 -*-
"""notes_store —— 本地 Markdown 笔记存储层（demo_new_project，2026-11-14）。

设计要点：
- 纯标准库，零第三方依赖；
- 所有文件严格限定在 notes 根目录内（resolve + startswith 越界防护）；
- 文件名 = slug 化标题 + 冲突自动 -2/-3 后缀，绝不覆盖已有笔记；
- UTF-8 读写，Markdown 正文带一级标题与创建时间元数据。
"""
import io
import os
import re
import time

_SLUG_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
_NAME_RE = re.compile(r"^[0-9a-zA-Z\u4e00-\u9fff][0-9a-zA-Z\u4e00-\u9fff._-]*$")


class NoteError(Exception):
    """笔记操作的可预期错误（标题为空 / 名字非法 / 笔记不存在）。"""


class NotesStore:
    """Markdown 笔记库：一个目录即一个库，一篇笔记一个 .md 文件。"""

    def __init__(self, root: str):
        self._root = os.path.abspath(str(root))
        os.makedirs(self._root, exist_ok=True)

    # ---- 路径安全 ----
    def _resolve(self, name: str):
        name = str(name or "").strip()
        if not name or not _NAME_RE.match(name):
            raise NoteError("非法笔记名：%r（只允许字母/数字/中文/._-）" % name)
        if ".." in name:
            raise NoteError("非法笔记名：%r（禁止路径穿越）" % name)
        path = os.path.normpath(os.path.join(self._root, name + ".md"))
        root_prefix = os.path.normpath(self._root) + os.sep
        if not str(path).startswith(root_prefix):
            raise NoteError("路径越界：%r" % name)
        return path

    # ---- 核心 API ----
    def create(self, title: str, body: str = "") -> str:
        """新建笔记，返回实际文件名（不含扩展名）。标题冲突自动加 -2/-3 后缀。"""
        title = str(title or "").strip()
        if not title:
            raise NoteError("标题不能为空")
        slug = _SLUG_RE.sub("-", title).strip("-") or "note"
        if len(slug) > 60:
            slug = slug[:60].rstrip("-")
        candidate = slug
        n = 1
        while os.path.exists(self._resolve(candidate)):
            n += 1
            candidate = "%s-%d" % (slug, n)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        content = "# %s\n\n- created: %s\n\n%s\n" % (title, stamp, str(body or "").rstrip())
        with io.open(self._resolve(candidate), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return candidate

    def read(self, name: str) -> str:
        """读取笔记全文；不存在抛 NoteError。"""
        path = self._resolve(name)
        if not os.path.exists(path):
            raise NoteError("笔记不存在：%s" % name)
        with io.open(path, "r", encoding="utf-8") as f:
            return f.read()

    def list_notes(self) -> list:
        """列出全部笔记（最新修改在前）：[{name, title, chars, mtime}]。"""
        out = []
        for fn in os.listdir(self._root):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(self._root, fn)
            if not os.path.isfile(path):
                continue
            try:
                with io.open(path, "r", encoding="utf-8") as f:
                    head = f.read(4096)
            except OSError:
                continue
            title = ""
            for line in head.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            out.append({
                "name": fn[:-3],
                "title": title or fn[:-3],
                "chars": os.path.getsize(path),
                "mtime": int(os.path.getmtime(path)),
            })
        out.sort(key=lambda x: -x["mtime"])
        return out
