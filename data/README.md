# data/ 目录说明（2026-11-01）

本目录下的 `moray.sqlite3` 与 `memory.sqlite3` 是 **0 字节历史空壳**（page_count=0，
无任何表），不代表系统真实数据，排查/备份时**不要**看这里。

真库都在 `D:\MoRay\server\data\`（由 `server/app/config.py` 的 DATA_DIR 定义）：

| 真库 | 内容 |
|---|---|
| `server\data\moray.sqlite3` | 会话/消息/usage_log/cloud_providers 等主库 |
| `server\data\memory.sqlite3` | memory_bank / FTS / vec0 向量记忆库 |
| `server\data\synapse.sqlite3` | 神经总线事件库 |
| `data\cloud_budget.db` | 云端预算台账（此文件在本目录，真实存在） |

排查命令示例（只读）：
```
D:\python3.12 -c "import sqlite3; c=sqlite3.connect('file:D:/MoRay/server/data/moray.sqlite3?mode=ro', uri=True); print(c.execute('select count(*) from messages').fetchone())"
```
