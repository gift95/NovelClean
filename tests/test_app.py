"""Web 层测试：上传、下载、zip、过期链接、非法文件名。"""
import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402

BOOK = (
    "书名\n作者：x\n"
    "第1张 开始\n　　他 说 了 一 句 话 。\n　　。。\n　　喜欢测试书：测试书。\n"
    "第2章 继续\n　　结束了。。\n"
).encode("utf-8")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "WORK_DIR", str(tmp_path))
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


def upload(client, name="a.txt", data=BOOK):
    return client.post("/api/clean", data={"files": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def test_index_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "校准净化" in r.data.decode("utf-8")


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_upload_returns_results_and_files(client):
    r = upload(client)
    assert r.status_code == 200
    d = r.get_json()
    res = d["results"][0]
    assert res["chapters"] == 2
    assert res["still_junk"] == 0
    assert res["still_space"] == 0
    assert res["bad_indent"] == 0
    assert res["clean_name"].endswith("_校对版.txt")

    c = client.get(res["clean_url"])
    assert c.status_code == 200
    body = c.data.decode("utf-8-sig")
    assert "他说了一句话。" in body
    assert "喜欢测试书" not in body
    assert body.startswith("书名")

    a = client.get(res["audit_url"])
    assert a.status_code == 200
    assert "喜欢测试书" in a.data.decode("utf-8")


def test_zip_contains_everything(client):
    d = upload(client).get_json()
    z = client.get(d["zip_url"])
    assert z.status_code == 200
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(z.data)).namelist()
    assert any(n.endswith("_校对版.txt") for n in names)
    assert any(n.endswith("_审计.txt") for n in names)
    assert "校准报告.md" in names


def test_conflicting_filename_not_overwritten(client):
    r = client.post("/api/clean", data={"files": [
        (io.BytesIO(BOOK), "same.txt"),
        (io.BytesIO(BOOK.replace("书名".encode(), "另一本".encode())), "same.txt"),
    ]}, content_type="multipart/form-data")
    d = r.get_json()
    assert len(d["results"]) == 2
    assert d["results"][0]["base"] != d["results"][1]["base"]


def test_bad_token_404(client):
    assert client.get("/api/download/deadbeef/x.txt").status_code == 404
    assert client.get("/api/zip/deadbeef").status_code == 404


def test_path_traversal_blocked(client):
    d = upload(client).get_json()
    token = d["token"]
    r = client.get("/api/download/%s/..%%2F..%%2Fapp.py" % token)
    assert r.status_code in (404, 308, 400)


def test_empty_upload_400(client):
    r = client.post("/api/clean", data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_unreadable_encoding_reported(client):
    r = upload(client, "bad.txt", b"\x81\x40\xff\xff\xff\xfe garbage \xff")
    d = r.get_json()
    assert d["results"][0].get("error") or d["results"][0].get("chapters") is not None
    assert r.status_code in (200, 400)


def test_report_mentions_warnings(client):
    txt = ("只有一段没有章节标题的文本。\n第二段。\n").encode("utf-8")
    d = upload(client, "flat.txt", txt).get_json()
    rep = client.get(d["report_url"]).data.decode("utf-8")
    assert "未识别到章节标题" in rep
