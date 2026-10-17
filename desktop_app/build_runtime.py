#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把两份 python-build-standalone（x86_64 + arm64，同版本）合并成**一个通用二进制**运行时，
再裁掉笔记 App 用不到的部分，输出到 desktop_app/runtime/python。

用法：
    python3 build_runtime.py <x86_64目录> <arm64目录> [输出目录]
    python3 build_runtime.py <单个目录> - [输出目录]      # 只做单架构 + 裁剪

要点：
  1. 两个架构的 stdlib .py 完全一样，所以只保留 arm64 那棵树做骨架，再把每个 Mach-O 文件
     用 lipo 合成胖二进制（bin/python3.13、libpython、lib-dynload/*.so …）。
  2. 用 ditto 复制以保住符号链接（bin/python3 -> python3.13）。
  3. 裁掉 test/、tkinter、idlelib、pip、tcl/tk 等，体积从 ~90MB 降到 ~35MB。
"""
import glob
import os
import shutil
import subprocess
import sys

TRIM_GLOBS = [
    # 整个目录
    "include", "share", "lib/pkgconfig",
    "lib/python3.13/test", "lib/python3.13/idlelib", "lib/python3.13/tkinter",
    "lib/python3.13/turtledemo", "lib/python3.13/ensurepip", "lib/python3.13/lib2to3",
    "lib/python3.13/pydoc_data", "lib/python3.13/site-packages",
    "lib/python3.13/unittest/test", "lib/python3.13/config-3.13-*",
    # Tcl/Tk 整套（笔记 App 不用 tkinter；版本号会随 build 变，所以用通配）
    "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*",
    "lib/libtcl*", "lib/libtk*", "lib/libitcl*",
    # tkinter 扩展模块 / C 测试模块
    "lib/python3.13/lib-dynload/_tkinter*",
    "lib/python3.13/lib-dynload/_test*",
    "lib/python3.13/lib-dynload/xxlimited*",
]
KEEP_BIN = {"python3", "python3.13", "python3.13-config", "python3-config"}

MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",   # 64/32 位小端
    b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",   # 大端
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",   # fat
}


def is_macho(path):
    try:
        with open(path, "rb") as f:
            return f.read(4) in MACHO_MAGICS
    except OSError:
        return False


def rel_files(root):
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if os.path.islink(p):
                continue
            out.add(os.path.relpath(p, root))
    return out


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    x86 = sys.argv[1]
    arm = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] not in ("-", "") else None
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "runtime", "python")

    for d in [x86] + ([arm] if arm else []):
        if not os.path.isdir(os.path.join(d, "bin")):
            print(f"✗ 不是 python-build-standalone 解包目录: {d}")
            return 1

    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    if arm:
        fx, fa = rel_files(x86), rel_files(arm)
        only_x, only_a = sorted(fx - fa), sorted(fa - fx)
        print(f"文件数 x86_64={len(fx)} arm64={len(fa)}")
        if only_x:
            print(f"  仅 x86_64 有 {len(only_x)} 个（以前者为准）: {only_x[:5]}")
        if only_a:
            print(f"  仅 arm64 有 {len(only_a)} 个（并入）: {only_a[:5]}")
        base, extra = arm, only_x
        # 1) 以 arm64 树为骨架（ditto 保留符号链接与权限）
        print("→ ditto arm64 骨架…")
        run(["/usr/bin/ditto", arm, out])
    else:
        print("→ 单架构模式（不做 lipo）")
        fx = rel_files(x86)
        base, extra = x86, []
        print("→ ditto 骨架…")
        run(["/usr/bin/ditto", x86, out])

    # 补齐只在 x86_64 出现的文件
    for rel in extra:
        src = os.path.join(x86, rel)
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    # 2) lipo 合成胖二进制
    merged = 0
    if arm:
        print("→ lipo 合并 Mach-O…")
        for rel in sorted(fx | fa):
            pa, pb = os.path.join(out, rel), os.path.join(x86, rel)
            if not (os.path.isfile(pa) and os.path.isfile(pb) and not os.path.islink(pa)):
                continue
            if not (is_macho(pa) and is_macho(pb)):
                continue
            tmp = pa + ".lipo"
            try:
                run(["/usr/bin/lipo", "-create", pa, pb, "-output", tmp])
            except subprocess.CalledProcessError as e:
                print(f"  ! lipo 失败 {rel}: {e.stderr.strip()[:120]}")
                if os.path.exists(tmp):
                    os.remove(tmp)
                continue
            os.replace(tmp, pa)
            os.chmod(pa, 0o755)
            merged += 1
        print(f"  合并 {merged} 个文件")

    # 3) 裁剪
    print("→ 裁剪…")
    victims = []
    for pat in TRIM_GLOBS:
        victims.extend(glob.glob(os.path.join(out, pat)))
    for p in sorted(set(victims)):
        if not os.path.exists(p):
            continue
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.remove(p)
    bindir = os.path.join(out, "bin")
    for fn in os.listdir(bindir):
        if fn in KEEP_BIN:
            continue
        p = os.path.join(bindir, fn)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            os.remove(p)
    # 去掉 __pycache__（.pyc 可再生成，省 ~18MB）
    for dirpath, dirnames, _ in os.walk(os.path.join(out, "lib")):
        for d in list(dirnames):
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d), ignore_errors=True)
                dirnames.remove(d)

    # 2.4) libpython3.13.dylib（34MB）在 install_only 里其实没人引用
    #      （解释器是静态链接的），确认无引用后删掉，能省掉近一半体积。
    dylib = os.path.join(out, "lib", "libpython3.13.dylib")
    if os.path.exists(dylib):
        refs = []
        for dirpath, _, files in os.walk(out):
            for fn in files:
                p = os.path.join(dirpath, fn)
                if p == dylib or os.path.islink(p) or not is_macho(p):
                    continue
                r = subprocess.run(["/usr/bin/otool", "-L", p],
                                   capture_output=True, text=True)
                if "libpython3.13.dylib" in r.stdout:
                    refs.append(p)
        if refs:
            print(f"  ! {len(refs)} 个文件引用 libpython3.13.dylib，保留它")
        else:
            os.remove(dylib)
            print("  删除无人引用的 libpython3.13.dylib（省 34MB）")

    # 4) 自检
    py = os.path.join(out, "bin", "python3")
    arch = run(["/usr/bin/lipo", "-archs", os.path.join(out, "bin", "python3.13")]).stdout.strip()
    print(f"→ 架构: {arch}")
    probe = run([py, "-c",
                 "import sys,sqlite3,json,http.server,html.parser;"
                 "print(sys.version.split()[0], sys.prefix)"])
    print(f"→ 运行: {probe.stdout.strip()}")
    size = run(["/usr/bin/du", "-sh", out]).stdout.split()[0]
    print(f"✅ 运行时就绪: {out} ({size})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
