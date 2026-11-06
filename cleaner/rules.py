"""规则层：只放常量与判定函数，不做 IO，方便单测。"""
import re

IND = "\u3000\u3000"
BOM3 = bytes([0xEF, 0xBB, 0xBF])
CN_NUM = "一二三四五六七八九十百千零两"
DIG = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
       "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "两": 2}

# 白名单压缩：只保留数字、字母、汉字。碎片里会被随机插 ; $ . ` \ 等符号，
# 用黑名单永远列不全，所以这里保留白名单。
KEEP_CH = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]")


def squash(s):
    return KEEP_CH.sub("", s)


# 强特征推广串（URL、群号、口号）
AD_STRONG = re.compile(
    r"夸克群|trxs\.site|kdocs\.cn|53ds\.com|搜书神器|加群方法|每天500本|每日更新夸克"
    r"|本书由网友上传|该章节未审核通过|内容未审核通过"
)

# 站点尾巴：「喜欢《书名》：书名。」（书名重复两遍），每章一行
FOOT = re.compile(r"^喜欢([^，。：]{2,30})：(\1)。$")

# 章节列表导航行
NAV_WORDS = ("上一章", "下一章")

# 分卷行：只作为分隔保留，不算章节，也不加正文缩进
VOL_RE = re.compile(r"^(?:分卷|第[0-9一二三四五六七八九十百千零两]+卷|卷[0-9一二三四五六七八九十]+)")

# 另一种章节写法：「1.标题」（无「第」无「章」），源站常见
DOT_HEAD = re.compile(r"^([0-9]{1,4})[.．、](.*)$")

# 编号写坏的标题残渣：`2.1　　谁会甘心？`（两位点号 + 紧跟全角缩进）。
# 真标题后面不会跟正文缩进，所以这行其实是正文，剥掉数字前缀即可。
DOT_HEAD_JUNK = re.compile(r"^[0-9]{1,4}[.．、][0-9]{1,4}\s*\u3000\u3000")

HEAD_RE = re.compile(r"(第)?([0-9]+|[一二三四五六七八九十百千零两]+)([章张回节行])(?:[ ]?(.*))?$")
HEAD_NONUM = re.compile(r"(第)([章张回节行])(?:[ ]?(.*))?$")

# 中文标点与汉字：水印空格只在这两者之间清理
CJK_OR_P = "[\u4e00-\u9fff，。！？、；：…—”‘’（）《》「」]"
SPACE_RE = re.compile(r"(?<=%s) (?=%s)" % (CJK_OR_P, CJK_OR_P))

# 行内被塞进正文中间的推广串：只切广告段，前后原文要接起来。
# 例：'…带到了2每 天 更 新 几 百 本 小 说 裙…读 书 003年的新宿'
# → '…带到了2003年的新宿'（「003」是原文数字，不能一起删）
INLINE_AD = re.compile(r"(?:每\s*天|每\s*日)\s*更\s*新[^。，！？\u3000]{0,80}?读\s*书")

# 水印口水话（源站插在章节里的话术，字符间随机插符号，必须在 squash 之后匹配）
WATER_PHRASE = re.compile(
    r"分享地址失效|请关注|最新最全|书库群|内裙|请勿相信|收藏本书|微信公众号"
    r"|公众号|加群方法|这里会第一时间|发分享新地址|发现分享|请来首发"
    r"|免费满|文件小说"
)

# 站点横幅：整行都是「本站整理，文本仅供试读，进群看书」这类公告，
# 长度往往超过 120 字、还带句读，只能靠固定话术认；真实正文不会写这几句。
BANNER_PHRASE = re.compile(r"文本仅供试读|进群看书|更多最新小说尽在|仅供试读" r"|品质保证.*进群|本书由.{0,20}整理")

# 行尾粘连的推广数字串，如 '。” 9 2 1 0 9 2 4 3 0'
TAIL_NUM_RE = re.compile(r" (?=(?:\d ?){6,}\d?$)")


def squash_map(s):
    """压缩后的字符串 + 每个保留字符在原文里的下标，用于在句中精确下刀。"""
    chars, pos = [], []
    for i, ch in enumerate(s):
        if not KEEP_CH.match(ch):
            chars.append(ch)
            pos.append(i)
    return "".join(chars), pos


def strip_inline_ad(s):
    """切掉句中的推广串，返回 (新行, 切了几段)。

    按 squash 定位，所以广告里被插入的 ' , / ` 等噪音不会让规则失效；
    切完两端原文直接相连（'…带到了2' + '003年的新宿' → '…带到了2003年的新宿'）。
    """
    cut = 0
    while cut < 3:
        k, pos = squash_map(s)
        m = re.search(r"(?:每天|每日)更新", k)
        if not m:
            break
        end = None
        for mm in re.finditer(r"读书", k[m.end():]):
            if mm.start() <= 80:
                end = m.end() + mm.end()
                break
        if end is None:
            break
        s = s[:pos[m.start()]] + s[pos[end - 1] + 1:]
        cut += 1
    return s, cut


def is_waterline(s):
    """源站水印口水话行：汉字之间被塞进空格（洗版痕迹）。

    只看「两个汉字之间夹着空格」的位置，不看空格的绝对数量：
    这样 '“小杰，我是你陆姐。” 9 2 1 0 9 2 4 3 0'（真句子 + 粘在尾巴的推广数字）、
    '“示巴02 06 09 破损。”'（数字串里的空格）、'咚~咚~咚~'（拟声）都不会被误判。
    """
    body = s.strip().lstrip("\u3000")
    cjk = len(re.findall(r"[\u4e00-\u9fff]", body))
    if cjk < 3:
        return False
    joined = sum(1 for _ in re.finditer(
        r"[\u4e00-\u9fff][ \u3000]+(?=[\u4e00-\u9fff])", body))
    if joined < 3:
        return False
    return joined / cjk >= 0.3


def is_waterphrase(s):
    """命中源站固定水印话术（只在「整章全垃圾」这种有旁证的地方用）。"""
    return bool(WATER_PHRASE.search(squash(s)))


def is_adline(s):
    """整行都是广告/水印，可以删掉。

    只认「固定话术 + 句内没有标点 + 不超过 24 字」。
    不看空格的密度：真实句子也可能被源站塞满空格（那种行第 5 段会把空格去掉、正文保留），
    按密度删会把正文误删。
    """
    body = s.strip().lstrip("\u3000")
    k = squash(body)
    if len(k) < 2:
        return False
    core = body.rstrip("。！？，、；;.:： ")
    if re.search(r"[，。！？、；;：:]", core):
        return False
    if not WATER_PHRASE.search(k):
        return False
    return len(k) <= 24


def is_banner(s):
    """整行站点横幅：带句读、通常几十上百字，靠固定话术认。

    带引号的对白一律免疫（正文里的台词可能正好提到「进群」之类）。
    """
    if "\u201c" in s or '"' in s or "\u2018" in s:
        return False
    k = squash(s)
    return bool(BANNER_PHRASE.search(k))



def is_footer(s):
    s = s.strip()
    if FOOT.match(s):
        return True
    return (len(s) < 60 and all(w in s for w in NAV_WORDS)
            and ("书签" in s or "章节列表" in s))


def is_frag(s):
    k = squash(s)
    return (("每天更新" in k or "每日更新" in k)
            and ("几百本" in k or "数百本" in k or "百多本" in k
                 or "几十本" in k or "小说" in k)
            and ("裙" in k or "群" in k or "公众号" in k
                 or "读书" in k or "工忠" in k or "工众" in k))


def cn2int(s):
    """中文数字/阿拉伯数字转 int；无法解析返回 None。"""
    if s.isdigit():
        return int(s)
    tot = num = 0
    for ch in s:
        if ch in DIG:
            num = DIG[ch]
        elif ch == "十":
            tot += (num or 1) * 10
            num = 0
        elif ch == "百":
            tot += (num or 1) * 100
            num = 0
        elif ch == "千":
            tot += (num or 1) * 1000
            num = 0
        else:
            return None
    return tot + num


def parse_head(line):
    """识别章节标题，返回 dict 或 None。

    关键排除：去掉 ASCII 空格/BOM 后以全角缩进开头的行一律当正文，
    否则「一张灵符…」「一回到庭院…」会被误认成第 1 张 / 第 1 回。
    """
    probe = line.lstrip(" \ufeff")
    if probe.startswith("\u3000"):
        return None
    s = probe.lstrip("\u3000")
    m = HEAD_NONUM.match(s)
    if m:
        title = (m.group(3) or "").strip()
        if len(title) > 60:
            return None
        return {"n": None, "mark": m.group(2), "title": title,
                "hasdi": True, "numtxt": "", "raw": s}
    m = HEAD_RE.match(s)
    if not m:
        return _parse_dot_head(s)
    hasdi, num, mark, title = m.group(1), m.group(2), m.group(3), (m.group(4) or "")
    if len(title) > 60:
        return None
    # 非「章」的标记（第N行/第N张…）若标题只剩标点，多半是正文句被误解析
    # （「第二行。」），直接判为正文；「章」保留，源站确有「第1312章 。」这种烂标题。
    if mark != "章" and title.strip() and not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", title):
        return None
    n = cn2int(num)
    if n is None:
        return None
    return {"n": n, "mark": mark, "title": title.strip(),
            "hasdi": bool(hasdi), "numtxt": num, "raw": s}


def _parse_dot_head(s):
    """「1.穿越伊始」式标题：整行独立、标题有汉字、不含网址。"""
    if "http" in s or "www." in s:
        return None
    m = DOT_HEAD.match(s)
    if not m:
        return None
    num, rest = m.group(1), m.group(2)
    # 「73.1.西游记…」这种二级编号，把多余的数字点号吃掉
    while True:
        m2 = DOT_HEAD.match(rest)
        if not m2 or len(m2.group(2)) < 2:
            break
        rest = m2.group(2)
    title = rest.strip()
    if not title or len(title) > 60:
        return None
    if not re.search(r"[\u4e00-\u9fff]", title):
        return None
    return {"n": int(num), "mark": "章", "title": title,
            "hasdi": False, "numtxt": num, "raw": s}
