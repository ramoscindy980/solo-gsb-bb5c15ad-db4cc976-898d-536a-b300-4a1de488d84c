"""双语 SRT 拆轨：把每块恰好两行的双语字幕拆成中文轨与英文轨。

规则：

- 每个字幕块正文必须恰好两行，分别归入中文轨与英文轨；
  行数不符时抛出带块号的 ValueError（调用方应拒绝预览/导出）。
- 归属代价：汉字落在英文轨、拉丁字母落在中文轨，每个字符各计 1 分；
  数字、标点等其它字符不计，因此纯数字/标点行两种归属等价。
- 相邻块的中英行上下顺序发生变化，每次另计 2 分。
- 全段动态规划求总代价最小的归属序列（非逐块独立判定）；
  总代价相同的平局按逐块「中文在上」优先裁决（英文在上的块数更少者
  优先、前驱平局取中文在上），使数字或标点行依邻块保持稳定。
- 导出两轨 SRT：保留原时间码与正文行内容，序号从 1 重新编号。
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .parsers import SrtItem

ORDER_CHANGE_COST = 2   # 相邻块中英行上下顺序变化的代价

ZH = "zh"
EN = "en"


@dataclass
class SplitRow:
    new_index: int        # 重新编号（从 1 开始）
    index: int            # 原 SRT 声明的序号
    start_tc: str
    end_tc: str
    zh_line: str          # 归入中文轨的正文行（原文不变）
    en_line: str          # 归入英文轨的正文行（原文不变）
    zh_first: bool        # True：中文行在上（原第一行）；False：英文行在上
    cost: int             # 本块错配数（错轨汉字数 + 错轨拉丁字母数）
    order_change: bool    # 与上一块相比上下顺序是否变化（首块恒 False）


@lru_cache(maxsize=None)
def _is_han(ch: str) -> bool:
    """汉字：CJK 统一表意文字及兼容表意文字（含各扩展区）。"""
    return unicodedata.name(ch, "").startswith(
        ("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")
    )


@lru_cache(maxsize=None)
def _is_latin(ch: str) -> bool:
    """拉丁字母（含全角；不含数字、标点与其它文字）。"""
    if not unicodedata.category(ch).startswith("L"):
        return False
    return unicodedata.name(ch, "").startswith(("LATIN", "FULLWIDTH LATIN"))


def _block_label(pos: int, declared: int) -> str:
    """块号描述：解析顺序位置，声明序号不一致时一并给出。"""
    if pos == declared:
        return f"第 {pos} 块"
    return f"第 {pos} 块（声明序号 {declared}）"


def split_subtitles(items: list[SrtItem]) -> list[SplitRow]:
    """确定每块两行的归属（中文轨/英文轨），使全段总代价最小。

    每块正文必须恰好两行，否则抛带块号的 ValueError（调用方应拒绝导出）。
    """
    if not items:
        return []

    # 每块两种归属的错配代价：costs[i][0] 中文在上，costs[i][1] 英文在上
    costs: list[tuple[int, int]] = []
    lines: list[tuple[str, str]] = []
    for pos, item in enumerate(items, start=1):
        body = tuple(item.body_lines)
        if len(body) != 2:
            raise ValueError(
                f"{_block_label(pos, item.index)}正文为 {len(body)} 行，"
                "双语拆轨要求每块正文恰好两行（一行中文、一行英文）"
            )
        first, second = body
        han1 = sum(_is_han(ch) for ch in first)
        han2 = sum(_is_han(ch) for ch in second)
        lat1 = sum(_is_latin(ch) for ch in first)
        lat2 = sum(_is_latin(ch) for ch in second)
        # 中文在上：第一行入中文轨（拉丁字母计错）+ 第二行入英文轨（汉字计错）
        zh_first_cost = lat1 + han2
        # 英文在上：第一行入英文轨（汉字计错）+ 第二行入中文轨（拉丁字母计错）
        en_first_cost = han1 + lat2
        costs.append((zh_first_cost, en_first_cost))
        lines.append((first, second))

    n = len(items)
    # 平局裁决：总代价相同者，英文在上的块数更少者优先（逐块中文在上）。
    # 把二元组 (总代价, 英文在上块数) 压成单标量：
    # score = 总代价 * (n+1) + 英文在上块数（块数 ≤ n，不会串位）
    width = n + 1
    trans = ORDER_CHANGE_COST * width

    # dp[s]：块 i 归属为 s（0 中文在上 / 1 英文在上）时前 i+1 块的最小 score
    dp = [costs[0][0] * width, costs[0][1] * width + 1]
    parents: list[tuple[int, int]] = [(-1, -1)]
    for i in range(1, n):
        c0, c1 = costs[i]
        # 候选 (score, 前驱状态)；元组比较使 score 相同时前驱 0（中文在上）优先
        best0 = min((dp[0], 0), (dp[1] + trans, 1))
        best1 = min((dp[0] + trans, 0), (dp[1], 1))
        dp = [best0[0] + c0 * width, best1[0] + c1 * width + 1]
        parents.append((best0[1], best1[1]))

    state = 0 if dp[0] <= dp[1] else 1   # 末块平局取中文在上
    states = [0] * n
    states[n - 1] = state
    for i in range(n - 1, 0, -1):
        state = parents[i][state]
        states[i - 1] = state

    rows: list[SplitRow] = []
    for i, state in enumerate(states):
        first, second = lines[i]
        zh_first = state == 0
        rows.append(
            SplitRow(
                new_index=i + 1,
                index=items[i].index,
                start_tc=items[i].start_tc,
                end_tc=items[i].end_tc,
                zh_line=first if zh_first else second,
                en_line=second if zh_first else first,
                zh_first=zh_first,
                cost=costs[i][state],
                order_change=i > 0 and state != states[i - 1],
            )
        )
    return rows


def split_summary(rows: list[SplitRow]) -> dict:
    """汇总：错配字符总数、顺序变化次数与全段总代价。"""
    mismatch = sum(r.cost for r in rows)
    changes = sum(1 for r in rows if r.order_change)
    return {
        "total": len(rows),
        "mismatch": mismatch,
        "order_changes": changes,
        "order_change_cost": changes * ORDER_CHANGE_COST,
        "total_cost": mismatch + changes * ORDER_CHANGE_COST,
    }


def build_split_srt(rows: list[SplitRow], track: str) -> str:
    """生成单轨 SRT：保留原时间码与正文行内容，从 1 重新编号。

    track 为 ``"zh"``（中文轨）或 ``"en"``（英文轨）。
    """
    if track not in (ZH, EN):
        raise ValueError(f"未知轨道「{track}」，应为 {ZH} 或 {EN}")
    blocks = []
    for row in rows:
        line = row.zh_line if track == ZH else row.en_line
        blocks.append(
            f"{row.new_index}\n{row.start_tc} --> {row.end_tc}\n{line}"
        )
    return "\n\n".join(blocks) + "\n"
