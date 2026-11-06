"""规则回归测试：每条用例都对应一个真实踩过的坑。"""
import pytest

from cleaner.pipeline import CleanError, clean_text, process, to_output_bytes


def ch(title, body="正文。"):
    return "第%s章 %s\n　　%s\n" % (title, title, body)


def build(*chapters):
    parts = []
    for c in chapters:
        parts.append("第%s章 标题%s\n　　这是一段正文。\n" % (c, c))
    return "".join(parts)


def test_indented_body_never_becomes_heading():
    text = (
        "第1章 开始\n"
        "　　一张灵符，两行清泪。\n"
        "　　一回到庭院，他就坐下。\n"
        "　　第十张三阶上品灵符。\n"
        "第2章 继续\n"
        "　　五行剑气。\n"
    )
    r = clean_text(text)
    assert r["verify"]["chapters"] == 2
    assert "　　一张灵符，两行清泪。" in r["text"]
    assert "　　一回到庭院，他就坐下。" in r["text"]
    assert "　　" not in [l[:1] for l in r["text"].split("\n") if l.startswith("第1章")]


def test_standalone_fragment_line_removed():
    frag = "每.天/更新 '几百本.小说 q.uN 8 5 '0,9'0^0`7 2'4 工忠;浩：5\\1 读'书"
    text = "第1章 开始\n　　正文一。\n%s\n　　正文二。\n" % frag
    r = clean_text(text)
    assert "工忠" not in r["text"]
    assert r["stats"]["drop_junk"] == 1
    assert "正文一。" in r["text"] and "正文二。" in r["text"]


def test_glued_fragment_keeps_body_paragraph():
    glued = "每 天 更 新 几 百 本 小 说 裙2`5.5^1*8`91*92 公 众,号　　沿着正方形肉块的边开始切。"
    text = "第1章 开始\n%s\n" % glued
    r = clean_text(text)
    assert r["stats"]["strip_prefix"] == 1
    assert "沿着正方形肉块的边开始切。" in r["text"]
    assert "裙2" not in r["text"]


def test_site_footer_per_chapter_removed():
    text = (
        "第1章 开始\n　　正文。\n　　喜欢测试书：测试书。\n"
        "第2章 继续\n　　正文。\n　　喜欢测试书：测试书。\n"
    )
    r = clean_text(text)
    assert r["stats"]["drop_junk"] == 2
    assert "喜欢测试书" not in r["text"]
    assert r["verify"]["chapters"] == 2


def test_nav_footer_removed():
    text = "第1章 开始\n　　正文。\n　　TXT下载上一章 ← 章节列表 → 下一章加入书签\n"
    r = clean_text(text)
    assert "上一章" not in r["text"]


def test_renumber_midway_reset():
    text = build(1046, 1047, 1, 1049, 1050)
    r = clean_text(text)
    nums = [v for v in r["verify"]["dups"]]
    assert nums == []
    assert r["verify"]["gaps"] == []
    assert r["stats"]["renum"] == 1
    assert "第1048章" in r["text"]


def test_renumber_tail_block():
    text = build(1, 2, 3, 4, 5, 1, 2, 3)
    r = clean_text(text)
    assert r["verify"]["dups"] == []
    assert r["verify"]["gaps"] == []
    assert r["stats"]["renum"] == 3
    assert "第8章" in r["text"]


def test_no_evidence_no_renumber():
    # 1,2,3,4 之后接 4：尾部重复且没有后继章可反推，不猜
    text = build(1, 2, 3, 4, 4)
    r = clean_text(text)
    assert r["stats"]["renum"] == 0


def test_duplicate_with_following_gap_is_filled():
    # 1,2,3,3,5：重号 + 后面缺 4 → 有证据，把第二个 3 补成 4
    text = build(1, 2, 3, 3, 5)
    r = clean_text(text)
    assert r["stats"]["renum"] == 1
    assert r["verify"]["dups"] == []
    assert r["verify"]["gaps"] == []



def test_watermark_spaces_between_hanzi():
    text = "第1章 开始\n　　他 说 了 一 句 话 ， 很 长 时 间 。\n"
    r = clean_text(text)
    assert "他说了一句话，很长时间。" in r["text"]
    assert r["stats"]["space"] == 11


def test_ascii_and_english_spaces_kept():
    text = "第1章 开始\n　　我用 iPhone 拍照，one piece 好看。\n"
    r = clean_text(text)
    assert "iPhone 拍照" in r["text"]
    assert "one piece" in r["text"]


def test_double_period_and_ellipsis():
    text = "第1章 开始\n　　结束了。。\n　　……\n　　好的。。。\n"
    r = clean_text(text)
    assert "结束了。\n" in r["text"]
    assert "　　……" in r["text"]
    assert "好的。。。" in r["text"]  # 三个句号不动


def test_quote_tail_fixed_only_when_unbalanced():
    text = "第1章 开始\n　　他说。“\n　　她说：“走吧。”\n"
    r = clean_text(text)
    assert "他说。”" in r["text"]
    assert "“走吧。”" in r["text"]
    assert r["stats"]["quote_tail"] == 1


def test_heading_marks_and_spacing():
    text = "第1张 标题一\n　　正文。\n第2章标题二\n　　正文。\n第一章 标题三\n　　正文。\n第章 标题四\n　　正文。\n"
    r = clean_text(text)
    assert "第1章 标题一" in r["text"]
    assert "第2章 标题二" in r["text"]
    assert "第3章 标题三" in r["text"] or "第1章 标题三" in r["text"]
    assert r["stats"]["head_fix_mark"] == 1
    assert r["stats"]["head_fix_space"] == 1
    assert r["stats"]["head_fix_nonum"] == 1
    assert r["verify"]["still_junk"] == 0


def test_head_zone_kept_and_not_counted_as_bad_indent():
    text = "测试书名\n作者：某人\n简介：\n一句简介。\n第1章 开始\n　　正文。\n"
    r = clean_text(text)
    assert r["text"].startswith("测试书名\n作者：某人")
    assert r["verify"]["bad_indent"] == 0


def test_tail_number_string_removed():
    text = "第1章 开始\n　　“小杰，我是你陆姐。” 9 2 1 0 9 2 4 3 0\n"
    r = clean_text(text)
    assert "9 2 1 0" not in r["text"]
    assert "我是你陆姐" in r["text"]


def test_bom_and_crlf_preserved():
    raw = b"\xef\xbb\xbf" + "第1章 开始\r\n　　正文。\r\n".encode("utf-8")
    r = process(raw, "a.txt")
    out = to_output_bytes(r, r["had_bom"])
    assert out.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in out
    assert b"\r\r" not in out
    assert r["encoding"] == "utf-8"


def test_gb18030_fallback():
    raw = "第1章 开始\n　　正文。\n".encode("gb18030")
    r = process(raw, "gbk.txt")
    assert r["encoding"] == "gb18030"
    assert r["had_bom"] is False
    assert "正文。" in r["text"]


def test_no_heading_returns_text_untouched():
    r = clean_text("只是一段普通文本。\n第二行。\n")
    assert r["verify"]["no_heading"] is True
    assert "只是一段普通文本。" in r["text"]


def test_empty_input_raises():
    with pytest.raises(CleanError):
        process(b"", "empty.txt")


def test_inline_ad_inside_sentence_keeps_text_digits():
    # 源站在句子中间插广告：'…带到了2' + 广告 + '003年的新宿'，两半要接回去，
    # 广告后面那几个数字属于原文，不能跟着广告一起删。
    text = ("第1章 开始\n"
            "　　凯姆和红龙带到了2每 天 更 新 几 百 本 小 说 裙2 5 51 89 192 公 众 号：5 1 读 书003年的新宿。\n")
    r = clean_text(text)
    assert "带到了2003年的新宿。" in r["text"]
    assert r["stats"]["strip_inline"] == 1
    assert r["verify"]["still_junk"] == 0


def test_inline_ad_only_line_is_dropped():
    text = "第1章 开始\n　　正文一。\n　　每 天 更 新 几 百 本 小 说 裙2 5 51 89 192 公 众 号：5 1 读 书\n　　正文二。\n"
    r = clean_text(text)
    assert "正文一。" in r["text"] and "正文二。" in r["text"]
    assert "读 书" not in r["text"] and "读书" not in r["text"]


def test_tail_offset_block_shifted_to_continue_previous():
    # 983 之后整段又从 954 连续排到结尾（源站两段编号错位 30）
    text = build(981, 982, 983) + build(954, 955, 956, 957)
    r = clean_text(text)
    assert r["verify"]["dups"] == []
    assert r["verify"]["gaps"] == []
    assert "第984章" in r["text"] and "第987章" in r["text"]


def test_single_tail_duplicate_not_renumbered():
    # 尾部只有一个重号（块长 < 3）→ 证据不足，不动
    text = build(1, 2, 3, 4, 5, 2)
    r = clean_text(text)
    assert r["stats"]["renum"] == 0


def test_verify_zeros_on_clean_book():
    text = (
        "书名\n作者：x\n简介：\n简介正文。\n"
        "第1章 开始\n　　他说。“\n　　……\n"
        "第2章 继续\n　　结束了。。\n"
    )
    r = clean_text(text)
    v = r["verify"]
    assert v["still_junk"] == 0
    assert v["still_space"] == 0
    assert v["bad_indent"] == 0
    assert v["dups"] == [] and v["gaps"] == []
    assert v["empty_chapters"] == 0


def test_fake_chapter_with_only_watermark_is_dropped():
    # 源站在真章节前插一个同号假章，正文全是水印话术 → 整章（标题+正文）删掉
    fake = ("第9章 什么战争已经结束了\n"
            "　　,如, 果 发,现分 享地  址失  效 了\n"
            "　　请关 注\n"
            "　　（公。众 , 号：5  1  读  书）\n")
    real = "第9章 什么？战争已经结束了？\n　　他真的打完了。\n　　所有人都不信。\n"
    text = "第8章 战前\n　　开打。\n" + fake + real
    r = clean_text(text)
    assert r["stats"]["drop_chapter"] == 1
    assert r["verify"]["dups"] == []
    assert "什么？战争已经结束了？" in r["text"]
    assert "请关 注" not in r["text"]


def test_short_watermark_line_dropped_but_onomatopoeia_kept():
    text = ("第1章 开始\n　　请关注\n　　最新最全请来首.发这   公。\n"
            "　　咚~咚~咚~\n　　他跑了。\n")
    r = clean_text(text)
    assert "请关注" not in r["text"] and "最新最全" not in r["text"]
    assert "咚~咚~咚~" in r["text"]      # 拟声词不能当水印删
    assert "他跑了。" in r["text"]


def test_inline_ad_with_noise_chars_is_still_cut():
    # 都市：广告串里被塞了 ' 和 / 空格，正则匹配不上，要靠 squash 定位
    text = ("第1章 开始\n"
            "　　“每'天更新 shu 百本小/说裙2 55、18 91 9 2 公/众号：5‘ 1'读  书冷静，他们人多。\n")
    r = clean_text(text)
    assert "冷静，他们人多。" in r["text"]
    assert "shu 百本" not in r["text"]
    assert r["stats"]["strip_inline"] == 1


def test_real_line_with_ad_number_tail_keeps_sentence():
    # '　　“小杰，我是你陆姐。” 9 2 1 0 9 2 4 3 0'：句子是真的，尾巴数字要切掉、句子要留
    text = "第1章 开始\n　　“小杰，我是你陆姐。” 9 2 1 0 9 2 4 3 0\n"
    r = clean_text(text)
    assert "我是你陆姐。" in r["text"]
    assert "9 2 1 0" not in r["text"]


def test_lead_ad_before_indented_text_is_cut():
    # 大明：广告塞在段落最前面，正文在第二处全角缩进之后
    ad = '　　每天更新一"百.多本:,交流\'裙"25:5!1"8,9,1:92若!曦\'备用6"82;4!32282'
    text = "第1章 开始\n" + ad + '　　　　“京城吏治需要整治。”\n'
    r = clean_text(text)
    assert "京城吏治需要整治。" in r["text"]
    assert "百.多本" not in r["text"]
    assert r["stats"]["strip_prefix"] == 1


def test_broken_dot_head_number_stays_prose():
    # 大明：'2.1　　谁会甘心？' 是正文，不是第2章；剥掉坏编号后不进标题序列
    text = ("第190章 众矢之的\n　　打完了。\n"
            "2.1　　谁会甘心？\n"
            "第191章 说蝼蚁！\n　　接着打。\n")
    r = clean_text(text)
    assert r["verify"]["dups"] == [] and r["verify"]["gaps"] == []
    assert r["verify"]["chapters"] == 2
    assert "谁会甘心？" in r["text"]
    assert "2.1" not in r["text"]
    assert r["stats"]["strip_prefix"] == 1


def test_head_banner_before_first_chapter_is_dropped():
    # 大明：整条推广横幅塞在书名之上、第一个标题之前
    banner = ('每?天.更,新一"百,多\'本若.曦:交:流裙25?5189,1;92若;曦备:用6"8;2:4,3?2282'
              '本书由书迷家族荣哥86269847整理，文本仅供试读！欢迎大家进群看书。')
    text = banner + "\n书名：大明\n作者：某某\n第1章 开始\n　　正文。\n"
    r = clean_text(text)
    assert r["text"].startswith("书名：大明")
    assert "荣哥" not in r["text"]
    assert r["verify"]["still_junk"] == 0
    assert r["stats"]["drop_junk"] == 1


def test_tail_site_banner_is_dropped_but_dialogue_kept():
    # 大明末行：整行横幅，带句读且超长，靠「文本仅供试读 / 进群看书」认
    text = ("第2章 结束\n　　打完了。\n"
            "　　本书由书迷家族荣哥86269847整理，文本仅供试读！书迷家族140+各类最新荣哥"
            "品质保证，欢迎大家进群看书。尽在书迷家族小说群更多最新小说尽在\n"
            "　　他说：“欢迎大家进群看书。”\n")
    r = clean_text(text)
    assert "文本仅供试读" not in r["text"]
    assert "欢迎大家进群看书。" in r["text"]   # 带引号对白免疫
    assert r["stats"]["drop_junk"] == 1
