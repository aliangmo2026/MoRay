# -*- coding: utf-8 -*-
"""notes —— 本地 Markdown 笔记管理 CLI（demo_new_project，2026-11-14）。

用法：
    python notes_cli.py create "标题" [--body "正文"] [-d 笔记目录]
    python notes_cli.py read   "笔记名"  [-d 笔记目录]
    python notes_cli.py list              [-d 笔记目录]

默认笔记目录 = 当前工作目录下的 ./notes（可用 -d 覆盖）。
退出码：0 成功；1 笔记不存在/非法名；2 用法错误。
"""
import argparse
import sys

from notes_store import NoteError, NotesStore


def _build_parser():
    # -d/--dir 主解析器与全部子解析器共享（parents 注入）：子命令前后都能传
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-d", "--dir", default="notes", help="笔记目录（默认 ./notes）")
    p = argparse.ArgumentParser(
        prog="notes", parents=[common],
        description="本地 Markdown 笔记管理 CLI（create / read / list）")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", parents=[common], help="新建笔记")
    c.add_argument("title", help="笔记标题（也是文件名 slug）")
    c.add_argument("--body", default="", help="Markdown 正文（可选）")

    r = sub.add_parser("read", parents=[common], help="读取笔记全文")
    r.add_argument("name", help="笔记名（文件名，不含 .md）")

    sub.add_parser("list", parents=[common], help="列出全部笔记（最新在前）")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    store = NotesStore(args.dir)
    try:
        if args.cmd == "create":
            name = store.create(args.title, args.body)
            print("created: %s" % name)
            return 0
        if args.cmd == "read":
            try:
                print(store.read(args.name))
                return 0
            except NoteError:
                # 容错：允许直接传标题——唯一命中即读，多义/不存在维持原错误
                hits = [it for it in store.list_notes()
                        if it["title"] == str(args.name).strip()]
                if len(hits) == 1:
                    print(store.read(hits[0]["name"]))
                    return 0
                raise
            return 0
        if args.cmd == "list":
            items = store.list_notes()
            if not items:
                print("(空笔记库)")
                return 0
            print("%-28s %-36s %8s" % ("name", "title", "chars"))
            for it in items:
                title = it["title"] if len(it["title"]) <= 34 else it["title"][:33] + "…"
                print("%-28s %-36s %8d" % (it["name"][:28], title, it["chars"]))
            return 0
        return 2
    except NoteError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
