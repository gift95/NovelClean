"""命令行批处理：python cli.py 输入目录 -o 输出目录"""
import argparse
import os
import sys

from cleaner.pipeline import CleanError, process, to_output_bytes


def iter_inputs(path):
    if os.path.isdir(path):
        for fn in sorted(os.listdir(path)):
            if fn.lower().endswith(".txt"):
                yield os.path.join(path, fn)
    elif os.path.isfile(path):
        yield path
    else:
        raise SystemExit("路径不存在：%s" % path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="小说 TXT 校准净化（命令行）")
    ap.add_argument("input", help="单个 txt 或包含 txt 的目录")
    ap.add_argument("-o", "--out", default="out", help="输出目录，默认 ./out")
    ap.add_argument("--quiet", action="store_true", help="只输出一行汇总")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    rows = []
    failed = 0

    for path in iter_inputs(args.input):
        fn = os.path.basename(path)
        base, _ = os.path.splitext(fn)
        try:
            raw = open(path, "rb").read()
            r = process(raw, fn)
        except CleanError as e:
            print("[跳过] %s：%s" % (fn, e))
            failed += 1
            continue

        clean_path = os.path.join(args.out, base + "_校对版.txt")
        audit_path = os.path.join(args.out, base + "_审计.txt")
        with open(clean_path, "wb") as fp:
            fp.write(to_output_bytes(r, r["had_bom"]))
        with open(audit_path, "w", encoding="utf-8", newline="\r\n") as fp:
            fp.write(r["audit_text"])

        v, s = r["verify"], r["stats"]
        rows.append((fn, v["chapters"], v["still_junk"], v["still_space"],
                     v["bad_indent"], len(v["dups"]), len(v["gaps"]), s["renum"]))
        if not args.quiet:
            print("=" * 72)
            print(fn)
            print("  编码 %s；%d → %d 字节；%d 章（%s–%s）"
                  % (r["encoding"], r["input_bytes"], r["output_bytes"],
                     v["chapters"], v["first"], v["last"]))
            print("  删广告/垃圾 %d；剥段前碎片 %d；剥句中内嵌广告 %d；去水印空格 %d；"
                  "编号复位 %d；删伪章节 %d"
                  % (s["drop_junk"], s["strip_prefix"], s["strip_inline"], s["space"],
                     s["renum"], s.get("drop_chapter", 0)))
            print("  校验：残留推广 %d；残留空格 %d；非规范缩进 %d；重号 %d；缺号 %d"
                  % (v["still_junk"], v["still_space"], v["bad_indent"],
                     len(v["dups"]), len(v["gaps"])))
            print("  → %s" % clean_path)
            if v.get("no_heading"):
                print("  ! 未识别到章节标题，已按普通文本原样输出")

    if args.quiet and rows:
        for r in rows:
            print("%s\t%d 章\t残留 %d/%d/%d\t重号 %d 缺号 %d"
                  % (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))
    print("\n完成 %d 本，失败 %d 本，输出目录：%s"
          % (len(rows), failed, os.path.abspath(args.out)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
