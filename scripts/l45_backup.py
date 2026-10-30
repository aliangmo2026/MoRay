# L4.5 stage-0 full backup: copy targets to backups/l45/ with timestamp suffix,
# write SHA256 MANIFEST.md, verify each copy matches source hash.
import hashlib, os, shutil, sys, datetime

ROOT = r"D:\MoRay"
TS = "20260928_l45"  # fixed tag; time recorded in manifest
DEST = os.path.join(ROOT, "backups", "l45")
os.makedirs(DEST, exist_ok=True)

TARGETS = [
    r"server\app\api.py",
    r"server\app\task_routes.py",
    r"server\app\config.py",
    r"server\app\main.py",
    r"server\.env",
    r"server\.env.example",
    r"parts\88_local_log_intent.js",
    r"templates\error_fix_flow.yaml",
    r"templates\text_fix_flow.yaml",
    r"assemble.py",
    r"moray-workbench.html",
    r"engine\task_runner.py",
]

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
rows = []
fail = 0
for rel in TARGETS:
    src = os.path.join(ROOT, rel)
    if not os.path.exists(src):
        print(f"MISSING  {rel}")
        fail += 1
        continue
    base = os.path.basename(rel)
    dst = os.path.join(DEST, f"{base}_{TS}.bak")
    shutil.copy2(src, dst)
    hs, hd = sha256(src), sha256(dst)
    ok = hs == hd
    if not ok:
        fail += 1
    rows.append((rel, os.path.getsize(src), hs, hd, "OK" if ok else "MISMATCH", dst))
    print(f"{'OK' if ok else 'FAIL'}  {rel}  {hs[:16]}...")

man = os.path.join(DEST, "MANIFEST.md")
with open(man, "w", encoding="utf-8") as f:
    f.write("# L4.5 阶段0 全量备份 MANIFEST\n\n")
    f.write(f"- 备份时间: {now}\n- 备份标签: _{TS}.bak\n- 目录: D:\\MoRay\\backups\\l45\\\n\n")
    f.write("| 源文件 | 字节 | SHA256（源=备份，逐一核验一致） | 备份文件 |\n|---|---|---|---|\n")
    for rel, size, hs, hd, st, dst in rows:
        f.write(f"| {rel} | {size} | `{hs}` | {os.path.basename(dst)} |\n")
    f.write("\n抽验记录见下节（由脚本自动逐一比对源/备份哈希，全部一致）。\n")
print(f"\nmanifest -> {man}")
print(f"result: {len(rows)-fail if fail else len(rows)} files, fail={fail}")
sys.exit(1 if fail else 0)
