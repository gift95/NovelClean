"""CLI 层：目录批处理与单文件都要能跑。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import iter_inputs, main  # noqa: E402


def test_iter_inputs_single_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("第1章 开始\n　　正文。\n", encoding="utf-8")
    assert list(iter_inputs(str(p))) == [str(p)]


def test_iter_inputs_only_txt_in_dir(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b.md").write_text("x", encoding="utf-8")
    got = list(iter_inputs(str(tmp_path)))
    assert len(got) == 1 and got[0].endswith("a.txt")


def test_main_single_file_writes_outputs(tmp_path):
    src = tmp_path / "书.txt"
    src.write_text("书名\n作者：某人\n第1章 开始\n　　他说 了 一 句 话 。\n",
                   encoding="utf-8")
    out = tmp_path / "out"
    rc = main([str(src), "-o", str(out), "--quiet"])
    assert rc == 0
    assert (out / "书_校对版.txt").exists()
    assert (out / "书_审计.txt").exists()
    assert "第1章" in (out / "书_校对版.txt").read_text(encoding="utf-8")


def test_main_missing_path_raises_systemexit():
    try:
        main(["不存在.txt", "-o", "out"])
    except SystemExit:
        return
    raise AssertionError("路径不存在时应退出")
