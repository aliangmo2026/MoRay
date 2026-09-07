# -*- coding: utf-8 -*-
import io

p = 'CHANGELOG.md'
lines = io.open(p, encoding='utf-8').readlines()
entry = '''## 3.18.6（2026-09-07）修复批次 6（残留收尾）：messages 宽松语义 / 对外版本自动校验 / 演示横幅极窄屏防折行

- **#1 GET /messages 恢复宽松**（api.py）：会话不存在时不再 404，一律返回 200 + 空数组；
  存在会话照常返回消息列表；DELETE/PUT/POST 等其余接口行为不变。验证（TestClient）：
  存在会话 → 200 含 1 条；删除后 GET → 200 {data:[]}；从未存在 id → 200 {data:[]}；
  DELETE 幽灵会话仍 404。
- **#2 MORAY_VERSION 对外版本自动校验**（config.py / assemble.py）：config 新增
  `PRODUCT_VERSION = "1.0.0"` 权威字段（与内部 BUILD=3.18.x 数值不同属正常，不互相比对）；
  assemble 幂等流程在内部构建号校验旁新增对外版本校验：产物内所有 MORAY_VERSION 定义
  必须彼此一致且等于 config.PRODUCT_VERSION，否则打印各来源差异并以非零码退出。
  负向验证：110 的 MORAY_VERSION 改 9.9.9 → 构建失败 exit 1，
  “构建失败：对外产品版本不一致 —— parts/110_polish.js MORAY_VERSION=9.9.9，
  server/app/config.py PRODUCT_VERSION=1.0.0（请统一后重试）”；改回 1.0.0 恢复通过，
  重复构建根 hash 稳定（幂等）。
- **#3 演示横幅极窄屏防折行**（assemble.py 注入模板）：横幅样式加入
  box-sizing:border-box;width:100%;max-width:100vw;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis —— 单行不折行、超长省略号截断、容器不溢出视口。
  （实测 min-width:fit-content 在 360px 下会把容器撑到内容宽 837 导致横向溢出，故以
  width:100%+max-width:100vw 替代。）验证（Tabbit 无头）：360px 宽横幅 360×25 单行
  nowrap+ellipsis 不溢出（截图 work/shots/banner_360.png）；1440px 1440×25 与之前一致；
  根/release 仍不含横幅（hash 分组不变）。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  hash：根=release=`40e005372cb0022f…`（幂等稳定），web 两份=`8ae00239ae60a8ee…`（横幅分组）。

'''
out = lines[:2] + [entry] + lines[2:]
io.open(p, 'w', encoding='utf-8', newline='').write(''.join(out))
print('batch6 changelog inserted')
