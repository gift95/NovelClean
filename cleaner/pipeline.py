"""核心流水线：字节进、成品字节出，全程无文件 IO，便于 Web/CLI/测试复用。"""
import re

from .rules import (
    AD_STRONG, BOM3, DOT_HEAD_JUNK, FOOT, HEAD_NONUM, IND, INLINE_AD, SPACE_RE,
    TAIL_NUM_RE, VOL_RE,
    is_adline, is_banner, is_footer, is_frag, is_waterline, is_waterphrase, parse_head,
    strip_inline_ad,
)

MAX_CHARS = 40_000_000  # 单本上限，超过直接报错，避免把服务打满

STAT_KEYS = (
    "drop_junk", "strip_prefix", "strip_inline", "space", "head_fix_mark",
    "head_fix_di", "head_fix_space", "cn2ar", "renum", "dot2", "quote_tail",
    "indent_fix", "dropped_other", "drop_punct", "drop_tail", "head_fix_nonum",
    "drop_chapter",
)


class CleanError(Exception):
    """输入无法处理（编码、超长、非文本等）。"""


def decode(raw):
    """返回 (text, encoding, had_utf8_bom)。失败抛 CleanError。"""
    if raw is None or not raw:
        raise CleanError("文件为空")
    head3 = raw[:3]
    if head3 == BOM3:
        try:
            return raw[3:].decode("utf-8"), "utf-8", True
        except UnicodeDecodeError:
            pass
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16").lstrip("\ufeff"), "utf-16", False
        except UnicodeDecodeError:
            raise CleanError("UTF-16 解码失败，文件可能已损坏")
    try:
        return raw.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gb18030"), "gb18030", False
    except UnicodeDecodeError:
        raise CleanError("既不是 UTF-8 也不是 GB18030，无法识别编码")


def _empty_stats():
    return dict((k, 0) for k in STAT_KEYS)


def clean_text(text):
    """清洗纯文本（LF 或 CRLF 都可），返回 dict。"""
    if len(text) > MAX_CHARS:
        raise CleanError("文本过长（%d 字符），超过 %d 上限" % (len(text), MAX_CHARS))

    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    lines = text.split("\n")

    stats = _empty_stats()
    audit = []  # (原行号 或 -1, 动作, 内容)

    # ---- 1) 去推广 / 去碎片 ----
    kept = []
    for i, l in enumerate(lines):
        if not l.strip():
            kept.append("")
            continue
        orig = l
        n_inline = 0
        # 句中推广串：按 squash 后的位置下刀，源站插进去的 ' / ` 空格不会让规则失效
        l, n_inline = strip_inline_ad(l)
        if n_inline:
            stats["strip_inline"] += n_inline
            audit.append((i, "剥内嵌广告", orig))
            if not l.strip(" \u3000"):
                stats["drop_junk"] += 1
                continue
        # 编号写坏的标题残渣：`2.1　　“狄大人说的是……”`。
        # 数字前缀后面紧跟正文缩进，说明这是正文而不是标题，剥掉前缀即可，
        # 既不会凭空多出一个「第2章」，也不动正文。
        m_junk = DOT_HEAD_JUNK.match(l)
        if m_junk:
            stats["strip_prefix"] += 1
            audit.append((i, "剥标题残渣", l[:m_junk.end()]))
            l = IND + l[m_junk.end():]
        if (AD_STRONG.search(l) and len(l) < 120) or is_footer(l):
            stats["drop_junk"] += 1
            audit.append((i, "删", l))
            continue
        # 整行站点横幅（「本站整理，文本仅供试读，进群看书」），带句读、超长，
        # 只能靠固定话术认；标题行与带引号的对白不参与判定
        if not parse_head(l) and is_banner(l):
            stats["drop_junk"] += 1
            audit.append((i, "删横幅", l))
            continue
        # 水印口水话（「如果发现分享地址失效了 / 请关注 / 公众号：51读书」这类），
        # 真实正文段带句读，不会被误判；章节标题本身永远不当广告删
        if not parse_head(l) and is_adline(l):
            stats["drop_junk"] += 1
            audit.append((i, "删", l))
            continue
        if is_frag(l):
            # 碎片串 + 全角缩进 + 正文挤在同一行：只切前缀，正文必须留下
            cut = None
            m = re.search(r"(读书|工忠|工众|书)\s*(?=\u3000\u3000)", l)
            if m:
                cut = m.end()
            else:
                # 碎片结尾字符不固定，退化为「任一处全角缩进之前都是碎片」。
                # 源站会把广告塞在正文句子最前面：`　　每天更新几百本…282　　　　“正文”`
                # 首个缩进在最开头，所以要从第二个缩进处下刀。
                for m2 in re.finditer(IND, l):
                    idx = m2.start()
                    if not (0 < idx <= 160):
                        continue
                    prefix = l[:idx]
                    if is_frag(prefix) and "。" not in prefix and "，" not in prefix:
                        cut = idx
                        break
            if cut:
                stats["strip_prefix"] += 1
                audit.append((i, "剥前缀", l[:cut]))
                kept.append(l[cut:])
                continue
            core = l[2:] if l.startswith(IND) else l
            # 整行独立、且没有句读，才判定为纯广告行
            if is_frag(core) and ("，" not in core and "。" not in core):
                stats["drop_junk"] += 1
                audit.append((i, "删", l))
                continue
        kept.append(l)

    # ---- 2) 书头（首个标题之前）：书名/作者/简介原样保留 ----
    first_head = None
    for k, l in enumerate(kept):
        if parse_head(l):
            first_head = k
            break

    clean_head = []
    if first_head is None:
        # 没有任何章节标题：不改缩进、不造章节，但仍清掉孤立标点这类碎屑
        kept2 = []
        for l in kept:
            s = l.strip()
            if s and len(s) <= 1 and s in "，。、；：,.;:":
                stats["drop_punct"] += 1
                audit.append((-1, "删孤立标点", s))
                continue
            kept2.append(l)
        out_text = "\n".join(kept2).strip("\n") + "\n" if kept2 else ""
        return {
            "text": out_text,
            "stats": stats,
            "audit": audit,
            "verify": {
                "chapters": 0, "first": None, "last": None,
                "dups": [], "gaps": [], "still_junk": 0,
                "still_space": 0, "bad_indent": 0, "empty_chapters": 0,
                "longest_chapter": 0, "unbalanced": [], "no_heading": True,
            },
        }

    head_zone = kept[:first_head]
    body = kept[first_head:]
    for l in head_zone:
        s = l.strip()
        if not s:
            continue
        if s in ("，", "。", "、"):
            stats["dropped_other"] += 1
            audit.append((-1, "删孤立标点", s))
            continue
        # 书头里也可能整条塞着推广横幅（book4 大明：横幅就在书名之上、第一个标题之前）。
        # 真实书名/作者/简介永远不会命中 is_frag / is_adline。
        if is_frag(l) or (AD_STRONG.search(l) and len(l) < 120) or is_adline(l):
            stats["drop_junk"] += 1
            audit.append((-1, "删书头横幅", l))
            continue
        clean_head.append(l)

    # ---- 3) 标题规范化 ----
    out = []
    prev_n = 0
    for l in body:
        if not l.strip():
            out.append("")
            continue
        h = parse_head(l)
        if not h:
            out.append(l)
            continue
        if h["mark"] != "章":
            stats["head_fix_mark"] += 1
            audit.append((-1, "标记", "%s→章 %s" % (h["mark"], h["raw"][:30])))
        if not h["hasdi"]:
            stats["head_fix_di"] += 1
            audit.append((-1, "补第", h["raw"][:30]))
        if not h["numtxt"].isdigit():
            stats["cn2ar"] += 1
        title = h["title"]
        # 标题里又粘了一遍「第N章」：'第205章 投资资金这不就来第205章 投资资金这不就来了'
        t2 = re.sub(r"第[0-9]+章[\s\u3000]+", "", title)
        if t2 != title and re.sub(r"[\s\u3000]", "", t2):
            title = re.sub(r"\s+", " ", t2).strip()
            audit.append((-1, "标题去重号", h["raw"][:40]))
        # 标题以冒号开头（'第350章 ：恢复杨姓'）——源站把冒号写在了标题前
        t3 = title.lstrip("：: 　")
        if t3 != title and t3:
            title = t3
            audit.append((-1, "标题去前冒号", h["raw"][:40]))
        # 章标记后紧跟文字（没有空格）才算「章后无空格」
        after_mark = re.match(
            r"^(?:第)?[0-9一二三四五六七八九十百千零两]+[章张回节行](.)", h["raw"])
        if after_mark and after_mark.group(1) != " ":
            stats["head_fix_space"] += 1
        if h["n"] is None:
            # 「第章 XXX」：编号被源站吞掉，按前后顺序补 prev+1
            n = prev_n + 1
            stats["head_fix_nonum"] += 1
            audit.append((-1, "补编号", "第章→第%d章 %s" % (n, title[:24])))
        else:
            n = h["n"]
        prev_n = n
        out.append(("HEAD", n, title))

    # ---- 3.5) 伪章节：紧跟同名真章节、正文全是水印广告的假章，整章删掉 ----
    # 源站常插一个「第N章」假章，正文只有「如果发现分享地址失效了 / 请关注 /
    # 公众号：51读书」这类水印话，紧接着才是真正的第N章。留着头就会重号。
    heads_pos = [i for i, o in enumerate(out) if isinstance(o, tuple)]
    drop_idx = set()
    for a in range(len(heads_pos) - 1):
        p, q = heads_pos[a], heads_pos[a + 1]
        if out[p][1] != out[q][1]:
            continue
        body = [l for l in out[p + 1:q] if isinstance(l, str) and l.strip()]
        if len(body) > 30:
            continue
        if not body:
            # 正文被第 1 段整行过滤光了，只剩一个与后章同号的空壳标题
            drop_idx.update(range(p, q))
            stats["drop_chapter"] += 1
            audit.append((-1, "删伪章节", "第%d章 %s（正文全为广告）"
                          % (out[p][1], out[p][2][:20])))
            continue
        junk = [l for l in body
                if is_waterphrase(l) or is_frag(l) or AD_STRONG.search(l)
                or (not re.search(r"[。！？]", l.strip())
                    and len(re.findall(r"[\u4e00-\u9fff]", l)) < 12)]
        if len(junk) >= min(2, len(body)) and len(junk) / len(body) >= 0.8:
            drop_idx.update(range(p, q))
            stats["drop_chapter"] += 1
            audit.append((-1, "删伪章节", "第%d章 %s（正文 %d 行全为广告）"
                          % (out[p][1], out[p][2][:20], len(body))))
    if drop_idx:
        out = [o for i, o in enumerate(out) if i not in drop_idx]
        while out and out[-1] == "":
            out.pop()

    # ---- 4) 编号复位：只在有序列证据时平移 ----
    nums = [o[1] for o in out if isinstance(o, tuple)]
    titles = [o[2] for o in out if isinstance(o, tuple)]
    cur = list(nums)
    mapping = {}  # 标题下标 → 新章号

    # 单趟修复：证据链只允许推送一次。多趟会让已修好的块在第二趟
    # 再被识别成「错位」继续平移（实测把都市那本的 381 处推到 812 处）。
    prev = 0
    i = 0
    while i < len(cur):
        n = cur[i]
        if n == prev + 1:
            prev = n
            i += 1
            continue
        j = i
        while j + 1 < len(cur) and cur[j + 1] == cur[j] + 1:
            j += 1
        L = j - i + 1
        new_start = prev + 1
        if j + 1 < len(cur):
            # 情形 A/B：整块错号或跳号。判据是「本块长度恰好补上缺口，
            # 且后继章号正好接在缺口之后」——526/527 后面接 428 这类
            # 错号块就是靠这条证据识别的，不能只看跳号方向。
            nxt = cur[j + 1]
            if nxt - L == new_start:
                for k in range(i, j + 1):
                    mapping[k] = new_start + (k - i)
                    cur[k] = mapping[k]
                prev = new_start + L - 1
                i = j + 1
                continue
        elif i > 0 and cur[i] < prev:
            # 情形 C：末尾整块（一直排到文件结尾）章号偏低/归零，没有后继章可比对。
            # 两种证据都要求它是一整块、不是孤立重号（L >= 3）：
            #   C1 落差 >= 块长：整块重排，按 prev+1 起顺序铺开；
            #   C2 只是一段连续区间的起点偏低（如 983 之后从 954 又连续排到结尾）：
            #      整体平移 prev+1 - cur[i]，保留块内原有的连续步长。
            if L < 3:
                prev = cur[j]
                i = j + 1
                continue
            shift = prev + 1 - cur[i]
            for k in range(i, j + 1):
                mapping[k] = cur[k] + shift
                cur[k] = mapping[k]
            prev = cur[j]
            i = j + 1
            continue
        prev = cur[j]
        i = j + 1

    stats["renum"] = len(mapping)
    head_positions = [x for x in range(len(out)) if isinstance(out[x], tuple)]
    for k in sorted(mapping):
        old, new = nums[k], mapping[k]
        pos = head_positions[k]
        audit.append((-1, "编号复位", "第%d章→第%d章 %s" % (old, new, titles[k][:24])))
        out[pos] = ("HEAD", new, out[pos][2])

    # ---- 5) 正文格式化：缩进 / 水印空格 / 尾巴数字 / 标点 ----
    res = []
    for l in out:
        if isinstance(l, tuple):
            n, t = l[1], l[2]
            res.append(("第%d章" % n + (" " + t if t else "")).rstrip())
            continue
        if not l.strip():
            res.append(l)
            continue
        s = l.lstrip(" \u3000")
        if not s.strip():
            res.append("")
            continue
        if len(s.strip()) <= 1 and s.strip() in "，。、；：,.;:":
            stats["drop_punct"] += 1
            audit.append((-1, "删孤立标点", s.strip()))
            continue
        # 只剩 '-' / '_' 这类分隔符的行，省略号是正文留白、`**` 这类也别动
        if re.fullmatch(r"[-_]{1,2}", s.strip()):
            stats["drop_punct"] += 1
            audit.append((-1, "删孤立符号", s.strip()))
            continue
        # 水印被切剩的残渣：'新：' 这种「一个汉字 + 冒号」，对白里的短句带引号，不会被误伤
        if re.fullmatch(r"[\u4e00-\u9fff]?[：:]", s.strip()):
            stats["drop_punct"] += 1
            audit.append((-1, "删残渣", s.strip()))
            continue
        if VOL_RE.match(s):
            # 分卷行是结构分隔，保留原样、不加正文缩进
            res.append(s.rstrip())
            continue
        if l != IND + s:
            stats["indent_fix"] += 1

        mt = TAIL_NUM_RE.search(s)
        if mt:
            audit.append((-1, "删尾号", s[mt.start():]))
            s = s[:mt.start()]
            stats["drop_tail"] += 1

        s2, k = SPACE_RE.subn("", s)
        stats["space"] += k

        s3 = re.sub(r"(?<!。)。。(?!。)", "。", s2)
        if s3 != s2:
            stats["dot2"] += 1
        # 行尾 “。”后误用开引号：行内开引号多于闭引号才翻
        if s3.endswith("。\u201c") and s3.count("\u201c") > s3.count("\u201d"):
            s3 = s3[:-1] + "\u201d"
            stats["quote_tail"] += 1
        res.append((IND + s3).rstrip())

    # ---- 6) 组装：标题前空一行，去掉尾部空行 ----
    final = list(clean_head)
    for l in res:
        if isinstance(l, str) and (re.match(r"^第[0-9]+章", l) or VOL_RE.match(l)):
            while final and final[-1] == "":
                final.pop()
            if final:
                final.append("")
        final.append(l)
    while final and final[-1].strip() == "":
        final.pop()

    out_text = "\n".join(final) + "\n" if final else ""

    # ---- 7) 校验 ----
    head_nums = [int(m.group(1)) for m in
                 (re.match(r"^第([0-9]+)章", l) for l in final) if m]
    gaps = [(head_nums[k - 1], head_nums[k]) for k in range(1, len(head_nums))
            if head_nums[k] - head_nums[k - 1] > 1]
    dups = [(head_nums[k - 1], head_nums[k]) for k in range(1, len(head_nums))
            if head_nums[k] <= head_nums[k - 1]]

    still_junk = sum(1 for l in final if AD_STRONG.search(l) or is_frag(l))
    still_space = sum(len(SPACE_RE.findall(l)) for l in final
                      if not re.match(r"^第[0-9]+章", l) and not VOL_RE.match(l.strip()))
    seen_head = False
    bad_indent = 0
    for l in final:
        if re.match(r"^第[0-9]+章", l) or VOL_RE.match(l.strip()):
            seen_head = True
            continue
        if seen_head and l.strip() and not l.startswith(IND):
            bad_indent += 1

    # 空章 + 最长章
    empty_ch = 0
    longest = 0
    k = 0
    while k < len(final):
        if re.match(r"^第[0-9]+章", final[k]):
            j = k + 1
            while j < len(final) and final[j] == "":
                j += 1
            if j >= len(final) or re.match(r"^第[0-9]+章", final[j]):
                empty_ch += 1
            else:
                size = 0
                while j < len(final) and not re.match(r"^第[0-9]+章", final[j]):
                    size += len(final[j])
                    j += 1
                longest = max(longest, size)
            k = j if j > k else k + 1
        else:
            k += 1

    # 章内引号平衡（只统计，不改）
    o = c = oq = cq = 0
    cur = None
    unbal = []
    for l in final:
        m = re.match(r"^第([0-9]+)章", l)
        if m:
            if cur and (o != c or oq != cq):
                unbal.append({"chapter": cur, "open": o, "close": c})
            cur = int(m.group(1))
            o = c = oq = cq = 0
            continue
        o += l.count("\u201c")
        c += l.count("\u201d")
        oq += l.count("\u2018")
        cq += l.count("\u2019")
    if cur and (o != c or oq != cq):
        unbal.append({"chapter": cur, "open": o, "close": c})

    return {
        "text": out_text,
        "stats": stats,
        "audit": audit,
        "verify": {
            "chapters": len(head_nums),
            "first": head_nums[0] if head_nums else None,
            "last": head_nums[-1] if head_nums else None,
            "dups": dups,
            "gaps": gaps,
            "still_junk": still_junk,
            "still_space": still_space,
            "bad_indent": bad_indent,
            "empty_chapters": empty_ch,
            "longest_chapter": longest,
            "unbalanced": unbal,
            "no_heading": False,
        },
    }


def audit_text(audit):
    """审计文件内容（tab 分隔，与本地脚本口径一致）。"""
    return "".join("%s\t原行%d\t%s\n" % (act, ln, s) for ln, act, s in audit)


def to_output_bytes(result, had_bom=True):
    """成品字节：CRLF + 可选的 UTF-8 BOM。"""
    data = result["text"].replace("\n", "\r\n").encode("utf-8")
    return (BOM3 + data) if had_bom else data


def process(raw, filename):
    """一次性完成：字节 → 结果字典（含成品字节与审计文本）。"""
    text, encoding, had_bom = decode(raw)
    result = clean_text(text)
    result["encoding"] = encoding
    result["had_bom"] = had_bom
    result["input_bytes"] = len(raw)
    result["output_bytes"] = len(to_output_bytes(result, had_bom))
    result["audit_text"] = audit_text(result["audit"])
    result["name"] = filename
    return result
