# -*- coding: utf-8 -*-
"""组装第二阶段应用层：拼接 parts -> 单个 <script> 块 -> 注入 HTML </body> 前
同时生成 PWA 配套文件（sw.js / manifest.webmanifest）"""
import io, os, re

PARTS = ['parts/10_db.js', 'parts/20_ai.js', 'parts/30_chat.js',
         'parts/40_compare_prompts.js', 'parts/50_snippets.js',
         'parts/60_workflow.js', 'parts/65_automation_page.js', 'parts/70_models_settings_boot.js',
         'parts/80_enhance.js',
         'parts/85_robust.js', 'parts/90_perf.js', 'parts/95_features.js',
         'parts/100_gateway.js', 'parts/110_polish.js',
         'parts/115_backend_sync.js',
         'parts/120_cost.js', 'parts/125_tools.js', 'parts/130_search.js',
         'parts/135_ui_polish.js']

chunks = []
for p in PARTS:
    with open(p, encoding='utf-8') as f:
        chunks.append(f.read())
app_js = '\n'.join(chunks)

# 追加：Service Worker 注册（PWA，仅 http/https 下生效）
app_js += """

/* ===================== [任务十] PWA / Service Worker ===================== */

/** 注册 Service Worker（仅 http/https 环境生效；file:// 打开时静默跳过）
 * @returns {void} */
function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  if (location.protocol !== 'http:' && location.protocol !== 'https:') return;
  navigator.serviceWorker.register('sw.js').then(reg => {
    console.log('[MoRay] Service Worker registered, scope:', reg.scope);
  }).catch(err => { console.warn('[MoRay] SW registration failed:', err); });
}
registerServiceWorker();
"""

HEADER = """
/* ============================================================
   MoRay v2.0 应用层（第二阶段）
   任务一 IndexedDB / 任务二 AI对接 / 任务三 对话 / 任务四 提示词
   任务五 片段 / 任务六 RAG / 任务七 工作流 / 任务八 模型管理
   任务九 设置 / 任务十 性能
   ============================================================ */
"""

full = HEADER + app_js

# ===================== [问题3] PWA 预检 + 原子构建 =====================
import sys, shutil

SRC = 'moray-workbench.html'
SW_TEMPLATE = 'sw_template.js'

# 运行时依赖（vendor 五库 + 本脚本生成的两份 PWA 文件）
RUNTIME_DEPS = [
    'vendor/highlight.min.js',
    'vendor/lucide.min.js',
    'vendor/marked.min.js',
    'vendor/purify.min.js',
    'vendor/tailwind-browser-4.js',
    'manifest.webmanifest',
    'sw.js',
]

# 1) PWA 模板预检：缺失 → 清晰中文报错并以非零码退出（不得抛原始 traceback）
if not os.path.exists(SW_TEMPLATE):
    print('构建失败：缺少 PWA 模板文件 ' + SW_TEMPLATE + '（请确认工程目录完整后重试）')
    sys.exit(1)

MANIFEST = """{
  "name": "MoRay — 本地AI开发者工作台",
  "short_name": "MoRay",
  "description": "本地优先的 AI 开发者工作台：对话、多模型对比、提示词库、RAG 知识库与自动化工作流",
  "start_url": "./moray-workbench.html",
  "display": "standalone",
  "background_color": "#0A0C14",
  "theme_color": "#0A0C14",
  "lang": "zh-CN",
  "icons": [
    { "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230A0C14'/%3E%3Ctext x='32' y='42' font-size='32' text-anchor='middle' fill='%233AD6E8'%3E%E2%97%88%3C/text%3E%3C/svg%3E", "sizes": "192x192", "type": "image/svg+xml" }
  ]
}
"""

# 2) 组装 html：幂等移除旧 script 注入块 + 注入新块 + manifest 链接（相对路径，file:// 可解析）
with open(SRC, encoding='utf-8') as f:
    html = f.read()

assert '</body>' in html
start_marker = '/* ============================================================\n   MoRay v2.0 应用层（第二阶段）'
if start_marker in html:
    s = html.index(start_marker)
    s = html.rindex('<script>', 0, s)
    e = html.index('</script>', s) + len('</script>')
    html = html[:s] + html[e:].lstrip('\n')
block = '<script>\n' + full + '\n</script>\n'
html = html.replace('</body>', block + '</body>')
if 'manifest.webmanifest' not in html:
    html = html.replace('<title>MoRay — 本地AI开发者工作台</title>',
        '<title>MoRay — 本地AI开发者工作台</title>\n<link rel="manifest" href="manifest.webmanifest">')
# [运行体检修复] 内联 SVG favicon：消灭每次 http 加载 favicon.ico 的 404 console error
# （file:// 双击打开同样无网络请求，行为一致；不改动任何 UI 设计）
if 'rel="icon"' not in html:
    html = html.replace('<link rel="manifest" href="manifest.webmanifest">',
        '<link rel="manifest" href="manifest.webmanifest">\n<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 64 64%27%3E%3Crect width=%2764%27 height=%2764%27 rx=%2714%27 fill=%27%230A0C14%27/%3E%3Ctext x=%2732%27 y=%2742%27 font-size=%2732%27 text-anchor=%27middle%27 fill=%27%233AD6E8%27%3E%E2%97%88%3C/text%3E%3C/svg%3E">')
# [问题4] 构建期图标名修正：本地 lucide 库不存在的 2 个名称 → 确定替换（HTML 模板中的 data-lucide 属性）
html = html.replace('data-lucide="code-review"', 'data-lucide="search-code"')
html = html.replace('data-lucide="message-square-question"', 'data-lucide="message-circle-question"')

# 3) 全部产物先写 .tmp（原子构建：任一步失败则删除临时文件、保留上一版正式产物）
staged = []
def stage(name, content):
    tmp = name + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(content)
    staged.append(tmp)

try:
    stage(SRC, html)
    # [批次修复 #5] SW 缓存名绑定 MORAY_BUILD：升级后浏览器自动换缓存，不依赖用户硬刷新
    sw_src = io.open(SW_TEMPLATE, encoding='utf-8').read()
    m_build = re.search(r"window\.MORAY_BUILD = '([^']+)'", app_js)
    build_ver = m_build.group(1) if m_build else 'dev'
    sw_src = re.sub(r"const CACHE_NAME = '[^']*'", "const CACHE_NAME = 'moray-" + build_ver + "'", sw_src, count=1)
    stage('sw.js', sw_src)
    stage('manifest.webmanifest', MANIFEST)

    # 4) 运行时依赖校验（通过后才允许替换正式文件）
    missing = [rel for rel in RUNTIME_DEPS if not os.path.exists(rel)]
    if missing:
        print('构建失败：缺少运行时依赖：' + ', '.join(missing))
        sys.exit(1)

    # [批次修复 #14] 内部版本三处一致性校验（110 MORAY_BUILD / 70 APP_VERSION / config BUILD）
    m110 = re.search(r"window\.MORAY_BUILD = '([^']+)'", app_js)
    p70 = io.open('parts/70_models_settings_boot.js', encoding='utf-8-sig').read()
    m70 = re.search(r"const APP_VERSION = 'v([^']+)'", p70)
    pcfg = io.open('server/app/config.py', encoding='utf-8').read()
    mcfg = re.search(r'BUILD = "([^"]+)"', pcfg)
    v110 = m110.group(1) if m110 else ''
    v70 = m70.group(1) if m70 else ''
    vcfg = mcfg.group(1) if mcfg else ''
    if not (v110 and v110 == v70 == vcfg):
        print('构建失败：内部版本号三处不一致 —— parts/110_polish.js MORAY_BUILD=' + v110 +
              '，parts/70_models_settings_boot.js APP_VERSION=v' + v70 +
              '，server/app/config.py BUILD=' + vcfg + '（请统一后重试）')
        sys.exit(1)

    # [批次修复 #14b] 对外产品版本校验：产物内全部 MORAY_VERSION 定义一致且等于 config PRODUCT_VERSION
    v_mv = set(re.findall(r"window\.MORAY_VERSION = '([^']+)'", app_js))
    mprod = re.search(r'PRODUCT_VERSION = "([^"]+)"', pcfg)
    v_prod = mprod.group(1) if mprod else ''
    if len(v_mv) > 1:
        print('构建失败：产物中存在多个 MORAY_VERSION 定义：' + ', '.join(sorted(v_mv)) + '（请统一）')
        sys.exit(1)
    v_mv_one = next(iter(v_mv)) if v_mv else ''
    if not (v_mv_one and v_prod and v_mv_one == v_prod):
        print('构建失败：对外产品版本不一致 —— parts/110_polish.js MORAY_VERSION=' + v_mv_one +
              '，server/app/config.py PRODUCT_VERSION=' + v_prod + '（请统一后重试）')
        sys.exit(1)

    # 5) 原子替换
    for tmp in staged:
        os.replace(tmp, tmp[:-4])
except BaseException:
    for tmp in staged:
        try:
            os.remove(tmp)
        except OSError:
            pass
    raise

# 6) 可选输出目录：与工程目录不同时自动拷贝运行时依赖（单拷 html 也可离线运行）
OUT_DIR = os.environ.get('MORAY_OUT_DIR', '')
if OUT_DIR and os.path.normpath(OUT_DIR) != os.path.normpath(os.path.dirname(os.path.abspath(SRC)) or '.'):
    os.makedirs(OUT_DIR, exist_ok=True)
    for rel in RUNTIME_DEPS:
        dst = os.path.join(OUT_DIR, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(rel, dst)
    shutil.copy2(SRC, os.path.join(OUT_DIR, os.path.basename(SRC)))
    print('已拷贝运行时依赖到输出目录:', OUT_DIR)

# 7) [问题4] 构建期 Lucide 图标校验：提取全部 data-lucide 名，kebab→PascalCase 对照本地库
import re as _re
def _pascal(name):
    return ''.join(p.capitalize() for p in name.split('-'))
def _icon_check(text, where):
    names = set(_re.findall(r'data-lucide="([a-z0-9-]+)"', text))
    bad = []
    for n in sorted(names):
        if _pascal(n) not in lucide_icons:
            bad.append(n)
    if bad:
        print('构建警告：' + where + ' 存在无效 Lucide 图标名：' + ', '.join(bad))
        return bad
    return []
lucide_icons = set()
try:
    with open('vendor/lucide.min.js', encoding='utf-8') as f:
        lucide_icons = set(_re.findall(r'([A-Z][A-Za-z0-9]*)\s*:', f.read()))
except Exception:
    lucide_icons = set()
_all_bad = _icon_check(html, 'HTML') + _icon_check(full, 'JS')
if _all_bad:
    print('构建失败：存在无效 Lucide 图标名：' + ', '.join(sorted(set(_all_bad))))
    sys.exit(1)
print('图标校验通过：' + str(len(set(_re.findall(r'data-lucide="([a-z0-9-]+)"', full + html)))) + ' 个图标名全部有效')

# 8) 语法自检（启发式）
opens = full.count('{') - full.count('}')
parens = full.count('(') - full.count(')')
brackets = full.count('[') - full.count(']')
print('assembled OK. app_js chars:', len(full))
print('balance {:', opens, ' (:', parens, ' [:', brackets)
print('total html lines:', html.count(chr(10)))
print('依赖校验通过：' + ', '.join(os.path.basename(r) for r in RUNTIME_DEPS))

# ===================== [BYOK] --web：一键导出可部署静态网站目录 =====================
# 用法：python assemble.py --web [目录名]（默认 web/）；不带参数 = 仅本地构建，行为不变。
import argparse

_parser = argparse.ArgumentParser(description='MoRay 构建脚本（parts -> 单文件 HTML）')
_parser.add_argument('--web', nargs='?', const='web', default=None, metavar='DIR',
                     help='导出可直接部署到静态托管的网站目录（默认 web/），含线上版入口 index.html')
_args, _unknown = _parser.parse_known_args()
if _unknown:
    print('忽略未知参数：' + ' '.join(_unknown))

if _args.web:
    web_dir = _args.web
    os.makedirs(web_dir, exist_ok=True)
    exported = []

    def _emit(rel_src, rel_dst, content=None):
        dst = os.path.join(web_dir, rel_dst)
        os.makedirs(os.path.dirname(dst) or web_dir, exist_ok=True)
        if content is None:
            shutil.copy2(rel_src, dst)
        else:
            with open(dst, 'w', encoding='utf-8') as f:
                f.write(content)
        exported.append(rel_dst)

    # vendor 五库（保持 vendor/ 子目录结构）
    for rel in RUNTIME_DEPS:
        if rel.startswith('vendor/'):
            _emit(rel, rel)
    # [打包修复] wallpapers/ 内置壁纸随站导出（默认 anime 壁纸由 CSS 引用，缺失会 404）
    if os.path.isdir('wallpapers'):
        for _root, _dirs, files in os.walk('wallpapers'):
            for fn in files:
                rel = os.path.relpath(os.path.join(_root, fn), '.').replace('\\', '/')
                _emit(rel, rel)
    # sw.js / 线上版 manifest（start_url "./"，入口 index.html）
    _emit('sw.js', 'sw.js')
    _emit('manifest.webmanifest', 'manifest.webmanifest', MANIFEST.replace('"start_url": "./moray-workbench.html"', '"start_url": "./"'))
    # [打包修复] 静态托管无本地后端：放一个 ok:false 的 api/health 桩，避免每次启动
    # 探测 /api/health 产生 404 console error；前端收到 ok:false 会正常显示“离线模式”。
    _emit('web/api/health', 'api/health', (
        '{\n'
        '  "ok": false,\n'
        '  "service": "moray-static",\n'
        '  "version": "static",\n'
        '  "build": "web",\n'
        '  "db": "offline",\n'
        '  "cloud_configured": false,\n'
        '  "counts": { "conversations": 0, "messages": 0 },\n'
        '  "message": "静态托管无本地后端：完整版请使用 启动MoRay.bat（后端 127.0.0.1:8000）"\n'
        '}\n'
    ))
    # 成品 html：线上入口 index.html + 本地名副本（SW 离线回退两处都能命中）
    # [批次修复 #5] 在线演示版横幅：--web 导出的成品注入不可关闭顶部横幅（静态站无本机 Agent）
    def _read_with_demo_banner():
        html = open(SRC, encoding='utf-8').read()
        style = (
            '<style>#demoModeBanner{position:sticky;top:0;z-index:9998;'
            'background:linear-gradient(90deg,#b98a2f,#8a6420);color:#fdf3d8;'
            'font-size:11px;text-align:center;padding:3px 10px;line-height:1.7;'
            'font-weight:500;letter-spacing:.2px;'
            'box-sizing:border-box;width:100%;max-width:100vw;'
            'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
            '}</style>'
        )
        banner = (
            '<div id="demoModeBanner">当前为在线演示版（纯前端 BYOK）——本机 Agent 能力需本地运行完整版'
            '（双击「启动MoRay.bat」或运行 server\\ 下 uvicorn 后访问 http://127.0.0.1:8000/）</div>'
        )
        html = html.replace('</head>', style + '</head>', 1)
        import re as _re
        html = _re.sub(r'<body[^>]*>', lambda m: m.group(0) + banner, html, count=1)
        return html
    _emit(SRC, 'index.html', _read_with_demo_banner())
    _emit(SRC, 'moray-workbench.html', _read_with_demo_banner())
    # deploy/（Worker 代理 + 部署说明）随包携带
    if os.path.isdir('deploy'):
        for root, _dirs, files in os.walk('deploy'):
            for fn in files:
                rel = os.path.relpath(os.path.join(root, fn), '.').replace('\\', '/')
                _emit(rel, rel)

    print('--- web/ 导出清单 ---')
    for rel in sorted(exported):
        print('  ' + rel)
    print('共 %d 个文件。将该目录拖到 Cloudflare Pages / Vercel（或推到任意静态托管）即可上线；' % len(exported))
    print('跨域代理见 ' + web_dir + '/deploy/README.md（BYOK：用户自填 Key，部署者零 token 费用）。')
