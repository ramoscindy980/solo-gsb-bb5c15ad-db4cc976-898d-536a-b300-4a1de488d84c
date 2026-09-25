"""双语 SRT 拆轨：归属 DP 单元测试（含暴力枚举对拍）+ 接口测试。

运行：python -m pytest tests/ -q
"""
import itertools
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.parsers import parse_srt
from app.split import (
    ORDER_CHANGE_COST,
    build_split_srt,
    split_subtitles,
    split_summary,
)

client = TestClient(app)


def _tc(ms):
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def _srt(spans, declared=None):
    """spans: [(start_ms, end_ms, text), ...]，text 内可含换行。"""
    blocks = []
    for i, (s, e, text) in enumerate(spans, start=1):
        idx = declared[i - 1] if declared else i
        blocks.append(f"{idx}\n{_tc(s)} --> {_tc(e)}\n{text}")
    return "\n\n".join(blocks) + "\n"


def _split(spans, declared=None):
    return split_subtitles(parse_srt(_srt(spans, declared)))


# ---------- 基本归属 ----------

def test_basic_split_zero_cost():
    rows = _split([
        (1000, 3000, "你好，世界！\nHello, world!"),
        (4000, 6000, "今天天气不错。\nNice weather today."),
    ])
    assert [r.zh_first for r in rows] == [True, True]
    assert [r.cost for r in rows] == [0, 0]
    assert rows[0].zh_line == "你好，世界！"
    assert rows[0].en_line == "Hello, world!"
    assert all(not r.order_change for r in rows)
    s = split_summary(rows)
    assert s["total"] == 2
    assert s["mismatch"] == 0
    assert s["order_changes"] == 0
    assert s["total_cost"] == 0


def test_flipped_block_pays_order_changes():
    rows = _split([
        (1000, 2000, "第一句。\nFirst sentence."),
        (3000, 4000, "Second line flipped.\n第二句被颠倒了。"),
        (5000, 6000, "第三句。\nThird sentence."),
    ])
    assert [r.zh_first for r in rows] == [True, False, True]
    assert [r.cost for r in rows] == [0, 0, 0]
    assert [r.order_change for r in rows] == [False, True, True]
    s = split_summary(rows)
    assert s["order_changes"] == 2
    assert s["order_change_cost"] == 2 * ORDER_CHANGE_COST
    assert s["total_cost"] == 2 * ORDER_CHANGE_COST


def test_flipped_segment_flips_together():
    # 连续多块上英下中：整段翻为英文在上，总代价 0
    rows = _split([
        (i * 2000, i * 2000 + 1000, f"English line {i}.\n第 {i} 句中文。")
        for i in range(1, 6)
    ])
    assert [r.zh_first for r in rows] == [False] * 5
    assert split_summary(rows)["total_cost"] == 0


def test_global_optimum_beats_per_block_choice():
    # 块 2 单独看「英文在上」错配 0 更优，但翻转引入 2 次顺序变化（4 分），
    # 高于保持中文在上的 3 分错配，全段最优应不翻转
    rows = _split([
        (1000, 2000, "第一句。\nFirst sentence."),
        (3000, 4000, "No.\n行。"),
        (5000, 6000, "第三句。\nThird sentence."),
    ])
    assert [r.zh_first for r in rows] == [True, True, True]
    assert rows[1].cost == 3          # "No." 2 个拉丁字母 + "行。" 1 个汉字
    assert split_summary(rows)["total_cost"] == 3


# ---------- 数字/标点行（无信息块） ----------

def test_neutral_block_follows_neighbors():
    rows = _split([
        (1000, 2000, "第一句。\nFirst."),
        (3000, 4000, "1 2 3\n4 5 6"),
        (5000, 6000, "第三句。\nThird."),
    ])
    assert [r.zh_first for r in rows] == [True, True, True]
    assert split_summary(rows)["total_cost"] == 0


def test_neutral_block_tie_prefers_zh_first():
    # 孤立无信息块：两种归属同价，平局裁决为中文在上
    rows = _split([(1000, 2000, "123\n456")])
    assert rows[0].zh_first is True
    assert rows[0].cost == 0


def test_neutral_block_between_opposite_neighbors_tie():
    # 块 2 无信息，两侧邻居顺序不同：两种归属都各产生 1 次顺序变化，
    # 平局裁决为中文在上
    rows = _split([
        (1000, 2000, "第一句。\nFirst."),
        (3000, 4000, "1 2 3\n4 5 6"),
        (5000, 6000, "Flipped line.\n被颠倒的句子。"),
    ])
    assert [r.zh_first for r in rows] == [True, True, False]
    s = split_summary(rows)
    assert s["order_changes"] == 1
    assert s["total_cost"] == ORDER_CHANGE_COST


# ---------- 错配计数 ----------

def test_mismatch_counting_picks_cheaper_side():
    rows = _split([(1000, 2000, "你好abc\n世界de")])
    # 中文在上：3 拉丁 + 2 汉字 = 5；英文在上：2 汉字 + 2 拉丁 = 4
    assert rows[0].zh_first is False
    assert rows[0].cost == 4
    assert rows[0].zh_line == "世界de"
    assert rows[0].en_line == "你好abc"


def test_digits_and_punctuation_are_free():
    rows = _split([(1000, 2000, "第 42 块牌子！\nSign No. 42?")])
    # 中文在上：上行无拉丁字母、下行无汉字 → 0
    assert rows[0].zh_first is True
    assert rows[0].cost == 0


# ---------- 行数校验 ----------

def test_line_count_rejected():
    with pytest.raises(ValueError, match="第 1 块.*1 行"):
        _split([(1000, 2000, "只有一行")])
    with pytest.raises(ValueError, match="第 2 块.*3 行"):
        _split([(1000, 2000, "甲\nA"), (3000, 4000, "一\n二\n三")])


def test_line_count_label_uses_declared_index():
    with pytest.raises(ValueError, match="第 2 块（声明序号 7）"):
        _split(
            [(1000, 2000, "甲\nA"), (3000, 4000, "只有一行")],
            declared=[3, 7],
        )


# ---------- 导出 ----------

def test_build_split_srt_keeps_tc_and_renumbers():
    rows = _split(
        [(1000, 2000, "你好。\nHello."), (3000, 4000, "Flipped.\n颠倒。")],
        declared=[5, 9],
    )
    zh = build_split_srt(rows, "zh")
    en = build_split_srt(rows, "en")
    assert zh.strip().split("\n\n") == [
        "1\n00:00:01,000 --> 00:00:02,000\n你好。",
        "2\n00:00:03,000 --> 00:00:04,000\n颠倒。",
    ]
    assert en.strip().split("\n\n") == [
        "1\n00:00:01,000 --> 00:00:02,000\nHello.",
        "2\n00:00:03,000 --> 00:00:04,000\nFlipped.",
    ]


def test_build_split_srt_rejects_unknown_track():
    rows = _split([(1000, 2000, "甲\nA")])
    with pytest.raises(ValueError, match="未知轨道"):
        build_split_srt(rows, "fr")


# ---------- 暴力枚举对拍：全段最优性与平局裁决 ----------

def _han_brute(ch):
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0xF900 <= cp <= 0xFAFF
    )


def _lat_brute(ch):
    return ("A" <= ch <= "Z") or ("a" <= ch <= "z")


def _brute_best(costs):
    """枚举全部归属序列，返回最优 (总代价, 英文在上块数)。"""
    n = len(costs)
    best = None
    for states in itertools.product((0, 1), repeat=n):
        total = sum(costs[i][states[i]] for i in range(n))
        total += ORDER_CHANGE_COST * sum(
            states[i] != states[i - 1] for i in range(1, n)
        )
        key = (total, sum(states))
        if best is None or key < best:
            best = key
    return best


def test_bruteforce_random_small_cases():
    rng = random.Random(20260925)
    pool = "你好世界今天abcXYZ12，.!"
    for _ in range(300):
        n = rng.randint(1, 8)
        spans = []
        costs = []
        for i in range(n):
            line1 = "".join(rng.choice(pool) for _ in range(rng.randint(1, 10)))
            line2 = "".join(rng.choice(pool) for _ in range(rng.randint(1, 10)))
            spans.append((i * 2000, i * 2000 + 1000, f"{line1}\n{line2}"))
            costs.append((
                sum(map(_lat_brute, line1)) + sum(map(_han_brute, line2)),
                sum(map(_han_brute, line1)) + sum(map(_lat_brute, line2)),
            ))
        rows = _split(spans)
        total = sum(r.cost for r in rows) + ORDER_CHANGE_COST * sum(
            r.order_change for r in rows
        )
        flips = sum(1 for r in rows if not r.zh_first)
        assert (total, flips) == _brute_best(costs)
        # 与 split_summary 的统计口径一致
        s = split_summary(rows)
        assert s["total_cost"] == total


# ---------- 接口 ----------

SRT_BILINGUAL = "\n".join(
    [
        "1",
        "00:00:01,000 --> 00:00:03,000",
        "你好，世界！",
        "Hello, world!",
        "",
        "2",
        "00:00:04,000 --> 00:00:06,000",
        "This one is flipped.",
        "这一块被颠倒了。",
        "",
        "3",
        "00:00:07,000 --> 00:00:09,000",
        "第三句。",
        "Third sentence.",
        "",
    ]
)


def test_api_split_preview():
    resp = client.post("/api/split", json={"srt": SRT_BILINGUAL})
    assert resp.status_code == 200
    data = resp.json()
    s = data["summary"]
    assert s["total"] == 3
    assert s["mismatch"] == 0
    assert s["order_changes"] == 2
    assert s["total_cost"] == 4
    rows = data["rows"]
    assert [r["zh_first"] for r in rows] == [True, False, True]
    assert [r["order_change"] for r in rows] == [False, True, True]
    assert rows[1]["zh_line"] == "这一块被颠倒了。"
    assert rows[1]["en_line"] == "This one is flipped."
    assert rows[0]["new_index"] == 1 and rows[0]["index"] == 1
    assert rows[0]["start_tc"] == "00:00:01,000"
    assert rows[0]["end_tc"] == "00:00:03,000"


def test_api_split_download_both_tracks():
    resp = client.post("/api/split.zh.srt", json={"srt": SRT_BILINGUAL})
    assert resp.status_code == 200
    assert "split-zh.srt" in resp.headers["content-disposition"]
    zh_blocks = resp.content.decode("utf-8").strip().split("\n\n")
    assert zh_blocks == [
        "1\n00:00:01,000 --> 00:00:03,000\n你好，世界！",
        "2\n00:00:04,000 --> 00:00:06,000\n这一块被颠倒了。",
        "3\n00:00:07,000 --> 00:00:09,000\n第三句。",
    ]

    resp = client.post("/api/split.en.srt", json={"srt": SRT_BILINGUAL})
    assert resp.status_code == 200
    assert "split-en.srt" in resp.headers["content-disposition"]
    en_blocks = resp.content.decode("utf-8").strip().split("\n\n")
    assert en_blocks == [
        "1\n00:00:01,000 --> 00:00:03,000\nHello, world!",
        "2\n00:00:04,000 --> 00:00:06,000\nThis one is flipped.",
        "3\n00:00:07,000 --> 00:00:09,000\nThird sentence.",
    ]


def test_api_split_bad_line_count_400():
    one_line = "1\n00:00:01,000 --> 00:00:02,000\n只有一行\n"
    for path in ("/api/split", "/api/split.zh.srt", "/api/split.en.srt"):
        resp = client.post(path, json={"srt": one_line})
        assert resp.status_code == 400
        assert "第 1 块" in resp.json()["detail"]

    three_lines = "1\n00:00:01,000 --> 00:00:02,000\n一\n二\n三\n"
    resp = client.post("/api/split", json={"srt": three_lines})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "第 1 块" in detail and "3 行" in detail


def test_api_split_line_count_uses_declared_index():
    srt = (
        "3\n00:00:01,000 --> 00:00:02,000\n甲\nA\n\n"
        "7\n00:00:03,000 --> 00:00:04,000\n只有一行\n"
    )
    resp = client.post("/api/split", json={"srt": srt})
    assert resp.status_code == 400
    assert "第 2 块（声明序号 7）" in resp.json()["detail"]


def test_api_split_empty_and_bad_srt():
    assert client.post("/api/split", json={"srt": ""}).status_code == 400
    resp = client.post("/api/split", json={"srt": "没有时间码"})
    assert resp.status_code == 400
    assert "SRT" in resp.json()["detail"]
