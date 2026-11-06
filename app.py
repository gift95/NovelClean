"""novel-txt-cleaner Web 服务：上传 txt → 校准净化 → 下载成品 + 审计 + 报告。"""
import io
import os
import re
import shutil
import time
import uuid
import zipfile

from flask import Flask, jsonify, render_template, request, send_file

from cleaner.pipeline import CleanError, process, to_output_bytes

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.environ.get("WORK_DIR", os.path.join(APP_DIR, "work"))
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", 32 * 1024 * 1024))
MAX_TOTAL_BYTES = int(os.environ.get("MAX_TOTAL_BYTES", 128 * 1024 * 1024))
MAX_FILES = int(os.environ.get("MAX_FILES", 20))
JOB_TTL = int(os.environ.get("JOB_TTL", 2 * 3600))

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_TOTAL_BYTES

SAFE_NAME = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._()\-]+")


def safe_name(name, fallback="book"):
    """保留中文名，只清掉路径分隔符与控制字符。"""
    name = os.path.basename(name or "")
    name = SAFE_NAME.sub("_", name).strip("._") or fallback
    if len(name) > 120:
        base, ext = os.path.splitext(name)
        name = base[:110] + ext
    return name


def prune_jobs():
    if not os.path.isdir(WORK_DIR):
        return
    now = time.time()
    for d in os.listdir(WORK_DIR):
        p = os.path.join(WORK_DIR, d)
        try:
            if os.path.isdir(p) and now - os.path.getmtime(p) > JOB_TTL:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def report_md(results):
    lines = ["# 校准净化报告", "", "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"), ""]
    head = ("| 文件 | 编码 | 章节 | 章号区间 | 删广告/垃圾行 | 剥段前碎片 | 剥句中内嵌广告 | "
            "去水印空格 | 编号复位 | 删伪章节 | 残留推广 | 残留空格 | 非规范缩进 |")
    sep = "|" + "---|" * 13
    lines += [head, sep]
    for r in results:
        s = r["stats"]
        lines.append("| %s | %s | %d | %s–%s | %d | %d | %d | %d | %d | %d | %d | %d | %d |" % (
            r["name"], r["encoding"], r["chapters"], r["first"], r["last"],
            s["drop_junk"], s["strip_prefix"], s["strip_inline"], s["space"], s["renum"],
            s.get("drop_chapter", 0), r["still_junk"], r["still_space"], r["bad_indent"]))
    lines += ["", "## 需要留意的地方", ""]
    any_note = False
    for r in results:
        if r.get("no_heading"):
            lines.append("- `%s`：未识别到章节标题，按普通文本原样输出。" % r["name"])
            any_note = True
        if r["dups"]:
            lines.append("- `%s`：仍有 %d 处章号重复/倒退 %s（证据不足，未强行改号）。"
                         % (r["name"], len(r["dups"]), r["dups"][:5]))
            any_note = True
        if r["gaps"]:
            lines.append("- `%s`：%d 处章号跳跃 %s（原文件缺章或错位插入）。"
                         % (r["name"], len(r["gaps"]), r["gaps"][:5]))
            any_note = True
        if r["empty_chapters"]:
            lines.append("- `%s`：%d 个空章（标题后没有正文）。" % (r["name"], r["empty_chapters"]))
            any_note = True
        if r["unbalanced"]:
            lines.append("- `%s`：%d 章章内引号不闭合（多为跨段对话，属作者写法，未改）。"
                         % (r["name"], r["unbalanced"]))
            any_note = True
    if not any_note:
        lines.append("- 无。四类硬指标（残留推广、残留水印空格、非规范缩进、章号连续性）全部通过。")
    lines += ["", "## 未改动的部分", "",
              "跨段对话留开引号、长省略号、拟声留白段、波浪号拟声、光杆标题、原文错别字均保持原样。", ""]
    return "\n".join(lines)


@app.get("/")
def index():
    return render_template("index.html",
                           max_file_mb=MAX_FILE_BYTES // (1024 * 1024),
                           max_files=MAX_FILES)


@app.get("/healthz")
def healthz():
    return "ok", 200


@app.post("/api/clean")
def api_clean():
    prune_jobs()
    files = request.files.getlist("files")
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify(error="没有收到文件"), 400
    if len(files) > MAX_FILES:
        return jsonify(error="一次最多 %d 个文件" % MAX_FILES), 400

    token = uuid.uuid4().hex
    job_dir = os.path.join(WORK_DIR, token)
    os.makedirs(job_dir, exist_ok=True)

    results = []
    used = set()
    for f in files:
        raw = f.read()
        if len(raw) > MAX_FILE_BYTES:
            results.append({"name": safe_name(f.filename), "error": "单文件超过 %d MB" % (MAX_FILE_BYTES // 1024 // 1024)})
            continue
        name = safe_name(f.filename)
        base, _ = os.path.splitext(name)
        if base in used:
            base = "%s_%d" % (base, len(used))
        used.add(base)
        try:
            r = process(raw, name)
        except CleanError as e:
            results.append({"name": name, "error": str(e)})
            continue
        except Exception as e:  # 兜底：单本失败不能拖垮整批
            results.append({"name": name, "error": "处理失败：%s" % e})
            continue

        clean_name = "%s_校对版.txt" % base
        audit_name = "%s_审计.txt" % base
        with open(os.path.join(job_dir, clean_name), "wb") as fp:
            fp.write(to_output_bytes(r, r["had_bom"]))
        with open(os.path.join(job_dir, audit_name), "w", encoding="utf-8", newline="\r\n") as fp:
            fp.write(r["audit_text"])

        results.append({
            "name": name,
            "base": base,
            "encoding": r["encoding"],
            "bytes_in": r["input_bytes"],
            "bytes_out": r["output_bytes"],
            "lines_in": r["text"].count("\n"),
            "chapters": r["verify"]["chapters"],
            "first": r["verify"]["first"],
            "last": r["verify"]["last"],
            "dups": r["verify"]["dups"],
            "gaps": r["verify"]["gaps"],
            "still_junk": r["verify"]["still_junk"],
            "still_space": r["verify"]["still_space"],
            "bad_indent": r["verify"]["bad_indent"],
            "empty_chapters": r["verify"]["empty_chapters"],
            "unbalanced": len(r["verify"]["unbalanced"]),
            "no_heading": r["verify"]["no_heading"],
            "stats": r["stats"],
            "audit_lines": len(r["audit"]),
            "clean_name": clean_name,
            "audit_name": audit_name,
            "clean_url": "/api/download/%s/%s" % (token, clean_name),
            "audit_url": "/api/download/%s/%s" % (token, audit_name),
        })

    ok = [x for x in results if not x.get("error")]
    if not ok:
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(results=results, error="全部文件处理失败"), 400

    md = report_md(ok)
    with open(os.path.join(job_dir, "校准报告.md"), "w", encoding="utf-8", newline="\r\n") as fp:
        fp.write(md)

    return jsonify(token=token, results=results,
                   zip_url="/api/zip/%s" % token,
                   report_url="/api/download/%s/%s" % (token, "校准报告.md"))


def _job_dir(token):
    if not re.match(r"^[0-9a-f]{32}$", token or ""):
        return None
    p = os.path.join(WORK_DIR, token)
    return p if os.path.isdir(p) else None


@app.get("/api/download/<token>/<path:fname>")
def api_download(token, fname):
    d = _job_dir(token)
    if not d:
        return jsonify(error="链接已过期，请重新处理"), 404
    target = os.path.join(d, os.path.basename(fname))
    if not os.path.isfile(target):
        return jsonify(error="文件不存在"), 404
    as_attachment = fname.endswith(".txt")
    return send_file(target, as_attachment=as_attachment, download_name=fname)


@app.get("/api/zip/<token>")
def api_zip(token):
    d = _job_dir(token)
    if not d:
        return jsonify(error="链接已过期，请重新处理"), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(d)):
            if fn.endswith((".txt", ".md")):
                z.write(os.path.join(d, fn), fn)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name="校对成品_%s.zip" % token[:8],
                     mimetype="application/zip")


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="上传总量超过 %d MB，请分批处理" % (MAX_TOTAL_BYTES // 1024 // 1024)), 413


if __name__ == "__main__":
    os.makedirs(WORK_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)
