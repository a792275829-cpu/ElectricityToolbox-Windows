"""海风节点均价及全省电价读取；供文件夹汇总与归类入口共用。"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from toolbox_dates import clearing_file_date
from toolbox_native_excel import load_value_workbook

NODE_TIME_SLOTS = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 15, 30, 45)]


def is_node_price_file(path: Path) -> bool:
    return (path.suffix.lower() == ".xlsx" and not path.name.startswith("~$")
            and "节点电价查询" in path.stem and "汇总" not in path.stem)


@dataclass(frozen=True)
class NodePriceSummary:
    market: str
    day: date
    prices: list[float]
    count: int
    province_prices: list[float]


def parse_node_prices(path: Path) -> NodePriceSummary:
    markets = [name for name in ("日前", "实时") if name in path.stem]
    if len(markets) != 1:
        raise ValueError(f"{path.name}：节点电价文件需明确日前或实时类型")
    workbook = load_value_workbook(path, read_only=True, data_only=True)
    nodes: dict[str, list[float]] = {}
    province_prices = None
    days: set[date] = set()
    try:
        for sheet_index, sheet in enumerate(workbook.worksheets):
            sheet.reset_dimensions()
            rows = iter(sheet.iter_rows())
            headers = [str(cell.value or "").strip() for cell in next(rows, ())]
            if "节点名称" not in headers or "数据项" not in headers:
                continue
            node_col, item_col = headers.index("节点名称"), headers.index("数据项")
            columns = []
            for slot in NODE_TIME_SLOTS:
                if headers.count(slot) != 1:
                    raise ValueError(f"{path.name} / {sheet.title}：96 点表头缺失或重复 {slot}")
                columns.append(headers.index(slot))
            sheet_matched = False
            for row in rows:
                node = str(row[node_col].value or "").strip() if len(row) > node_col else ""
                province = sheet_index == 1 and node == "全省"
                if not province and not any(word in node for word in ("海风", "升压站")):
                    continue
                if len(row) <= item_col or "电价" not in str(row[item_col].value):
                    raise ValueError(f"{path.name} / {node}：数据项不是电价")
                if (province and province_prices is not None) or (not province and node in nodes):
                    raise ValueError(f"{path.name}：节点重复 {node}，停止汇总")
                values = []
                for slot, column in zip(NODE_TIME_SLOTS, columns):
                    raw = row[column].value if len(row) > column else None
                    try:
                        if raw is None or isinstance(raw, bool):
                            raise ValueError("empty or boolean")
                        value = float(raw)
                        if not math.isfinite(value):
                            raise ValueError("non-finite")
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"{path.name} / {node} / {slot}：电价缺失或无效") from exc
                    values.append(value)
                if province:
                    province_prices = values
                else:
                    nodes[node] = values
                sheet_matched = True
            if sheet_matched:
                day = clearing_file_date(Path(sheet.title))
                if day is None:
                    raise ValueError(f"{path.name} / {sheet.title}：无法识别表格日期")
                days.add(day)
    finally:
        workbook.close()
    if not nodes:
        raise ValueError(f"{path.name}：没有找到名称含海风或升压站的节点")
    if province_prices is None:
        raise ValueError(f"{path.name}：第二个工作表中没有找到全省 96 点电价")
    if len(days) != 1:
        raise ValueError(f"{path.name}：节点数据涉及多个表格日期")
    day = days.pop()
    filename_day = clearing_file_date(path)
    if filename_day is not None and filename_day != day:
        raise ValueError(f"{path.name}：文件名日期与表格日期不一致")
    return NodePriceSummary(markets[0], day,
                            [math.fsum(values[i] for values in nodes.values()) / len(nodes)
                             for i in range(96)], len(nodes), province_prices)


def collect_node_prices(folder: Path, logger) -> dict[str, NodePriceSummary]:
    results = {}
    sources = {}
    folders = [folder, *sorted(path for path in folder.iterdir() if path.is_dir() and not path.is_symlink())]
    for directory in folders:
        for path in sorted(directory.iterdir()):
            if not path.is_file() or not is_node_price_file(path):
                continue
            result = parse_node_prices(path)
            if result.market in results:
                raise ValueError(f"海风{result.market}节点电价来源冲突：{sources[result.market]} 与 {path}，请仅保留一份来源")
            results[result.market] = result
            sources[result.market] = path
            logger(f"节点电价：{path.name}，{result.day}，筛选 {result.count} 个节点，按 96 点分别求平均")
            logger(f"全省电价：{path.name}，{result.day}，直接读取第二个工作表的 96 点电价")
    return results
