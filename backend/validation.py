"""Input validation for scheduling requests.

Separates *input errors* (malformed data the engineer must fix in the
form) from *infeasibility* (valid data for which no executable timeline
exists).
"""

from __future__ import annotations

from typing import Any

from solver import Exposure, Link, INF


class ValidationError(Exception):
    def __init__(self, errors: list[dict]):
        super().__init__("validation failed")
        self.errors = errors


def _err(errors: list[dict], code: str, message: str, **where: Any) -> None:
    item: dict[str, Any] = {"code": code, "message": message}
    item.update(where)
    errors.append(item)


def _is_int(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    return isinstance(v, int)


def validate(payload: Any) -> tuple[list[Exposure], list[Link]]:
    errors: list[dict] = []

    if not isinstance(payload, dict):
        raise ValidationError(
            [{"code": "bad_payload", "message": "请求体必须是 JSON 对象。"}]
        )

    raw_exposures = payload.get("exposures")
    raw_links = payload.get("links", [])

    if not isinstance(raw_exposures, list):
        raise ValidationError(
            [
                {
                    "code": "missing_exposures",
                    "message": "exposures 字段必须是数组。",
                }
            ]
        )

    n = len(raw_exposures)
    if not 5 <= n <= 10:
        _err(
            errors,
            "count_out_of_range",
            f"曝光数量必须在 5 到 10 项之间，当前为 {n} 项。",
        )

    exposures: list[Exposure] = []
    names: list[str] = []

    for idx, item in enumerate(raw_exposures):
        path = f"exposures[{idx}]"
        if not isinstance(item, dict):
            _err(errors, "bad_exposure", "曝光项必须是对象。", path=path)
            continue

        name = item.get("name", "").strip() if isinstance(item.get("name"), str) else None
        if not name:
            _err(
                errors,
                "bad_name",
                "曝光名称不能为空。",
                path=f"{path}.name",
            )
            name = name or f"#{idx + 1}"
        if name in names:
            _err(
                errors,
                "duplicate_name",
                f"曝光名称「{name}」重复，名称必须唯一。",
                path=f"{path}.name",
            )
        names.append(name)

        int_fields = {
            "duration": ("持续时间", 1, None),
            "est": ("最早开始时刻", 0, None),
            "lst": ("最晚开始时刻", 0, None),
            "cooldown": ("冷却时间", 0, None),
        }
        vals: dict[str, int] = {}
        for fld, (label, low, high) in int_fields.items():
            v = item.get(fld)
            if not _is_int(v):
                _err(
                    errors,
                    "bad_integer",
                    f"{label}必须是整数。",
                    path=f"{path}.{fld}",
                )
                continue
            if v < low:
                _err(
                    errors,
                    "bad_range",
                    f"{label}必须 ≥ {low}，当前为 {v}。",
                    path=f"{path}.{fld}",
                )
            vals[fld] = v

        equipment = item.get("equipment")
        if not isinstance(equipment, str) or not equipment.strip():
            _err(
                errors,
                "bad_equipment",
                "所用设备名称不能为空。",
                path=f"{path}.equipment",
            )
            equipment = equipment.strip() if isinstance(equipment, str) else "?"

        if all(k in vals for k in ("est", "lst")) and vals["est"] > vals["lst"]:
            _err(
                errors,
                "inverted_window",
                f"最早开始时刻（{vals['est']}）不能晚于最晚开始时刻"
                f"（{vals['lst']}）。",
                path=f"{path}.est",
            )

        if all(k in vals for k in int_fields):
            exposures.append(
                Exposure(
                    name=name,
                    duration=vals["duration"],
                    est=vals["est"],
                    lst=vals["lst"],
                    equipment=equipment,
                    cooldown=vals["cooldown"],
                )
            )

    links: list[Link] = []
    if not isinstance(raw_links, list):
        _err(errors, "bad_links", "links 字段必须是数组。")
    else:
        seen_pairs: set[tuple[int, int]] = set()
        for idx, item in enumerate(raw_links):
            path = f"links[{idx}]"
            if not isinstance(item, dict):
                _err(errors, "bad_link", "衔接约束必须是对象。", path=path)
                continue
            a, b = item.get("a"), item.get("b")
            if not _is_int(a) or not _is_int(b):
                _err(
                    errors,
                    "bad_link_index",
                    "衔接双方必须是曝光序号（整数）。",
                    path=path,
                )
                continue
            if not 0 <= a < n or not 0 <= b < n:
                _err(
                    errors,
                    "link_index_out_of_range",
                    f"衔接序号超出范围（应在 0–{n - 1}）。",
                    path=path,
                )
                continue
            if a == b:
                _err(
                    errors,
                    "self_link",
                    "衔接约束不能指向曝光自身。",
                    path=path,
                )
                continue
            if (a, b) in seen_pairs or (b, a) in seen_pairs:
                _err(
                    errors,
                    "duplicate_link",
                    f"曝光 {names[a]} 与 {names[b]} 之间的衔接约束重复。",
                    path=path,
                )
                continue
            seen_pairs.add((a, b))

            min_gap = item.get("min_gap", 0)
            max_gap = item.get("max_gap", None)
            if not _is_int(min_gap):
                _err(
                    errors,
                    "bad_integer",
                    "最小衔接间隔必须是整数。",
                    path=f"{path}.min_gap",
                )
                continue
            if max_gap is None:
                max_gap = INF
            elif not _is_int(max_gap):
                _err(
                    errors,
                    "bad_integer",
                    "最大衔接间隔必须是整数（或留空表示不限）。",
                    path=f"{path}.max_gap",
                )
                continue
            if min_gap > max_gap:
                _err(
                    errors,
                    "inverted_gap",
                    f"最小衔接间隔（{min_gap}）不能大于最大衔接间隔"
                    f"（{max_gap}）。",
                    path=path,
                )
                continue
            links.append(Link(a=a, b=b, min_gap=min_gap, max_gap=max_gap))

    if errors:
        raise ValidationError(errors)
    return exposures, links
