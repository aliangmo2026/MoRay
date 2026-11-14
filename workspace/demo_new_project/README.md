# notes —— 本地 Markdown 笔记管理 CLI

极简本地笔记工具：一篇笔记 = 一个 Markdown 文件。纯 Python 标准库，零依赖。

## 用法

```bash
# 新建（标题即文件名 slug，重名自动 -2/-3 后缀）
python notes_cli.py create "会议纪要" --body "决定：下周发布。"

# 读取
python notes_cli.py read "会议纪要"

# 列出（最新修改在前）
python notes_cli.py list
```

- 笔记目录默认 `./notes`，`-d <目录>` 可指定（测试用 `-d _test_data`）。
- 退出码：0 成功 / 1 笔记不存在或名字非法 / 2 用法错误。

## 安全设计

- 所有文件严格限定在笔记根目录内（resolve + 前缀校验，`..`、`/`、`\` 一律拒绝）；
- 重名绝不覆盖已有笔记；
- UTF-8 读写；正文自带 `# 标题` 与创建时间。

## 测试

```bash
python -m pytest test_notes_cli.py -v
# 或
python -m unittest test_notes_cli -v
```

测试数据只落在项目内 `_test_data/`，测完自动清理。
