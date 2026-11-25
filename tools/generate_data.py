"""生成扩充后的合成业务资料。

本脚本只写入 data/ 下的 JSON 文件，不被业务包导入。
原始记录（c-001..c-003、s-001..s-003、o-001..o-002、sites.json）
保持字段与顺序不变，新记录以追加方式进入同一资料包。

运行：python3 tools/generate_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# 夜间窗口 22:00 至次日 06:00，每小时一个槽位
SLOT_HOURS = [22, 23, 0, 1, 2, 3, 4, 5]
NIGHTS = ["2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06"]


def slot_ts(night: str, index: int) -> str:
    """返回某夜间窗口第 index 个槽位的本地时间戳。"""
    from datetime import datetime, timedelta

    start = datetime.fromisoformat(night + "T22:00")
    return (start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------------------
# sites.json：保留原始两条记录，追加 N-03
# ---------------------------------------------------------------------------
SITES = [
    {"site_id": "N-01", "district": "east", "boundary_version": "v1", "active": True,
     "patrol_window_start": "22:00", "patrol_window_end": "06:00"},
    {"site_id": "N-02", "district": "west", "boundary_version": "v1", "active": True,
     "patrol_window_start": "22:00", "patrol_window_end": "06:00"},
    # N-03 为居民深眠区，00:00 后不安排到场巡查，避免次生扰民
    {"site_id": "N-03", "district": "north", "boundary_version": "v1", "active": True,
     "patrol_window_start": "22:00", "patrol_window_end": "00:00"},
]

# ---------------------------------------------------------------------------
# site_versions.json：边界版本与汇水分区，v2 是更正版本
# ---------------------------------------------------------------------------
SITE_VERSIONS = [
    {
        "version_id": "sv-n01-v1",
        "site_id": "N-01",
        "boundary_version": "v1",
        "catchment_zones": ["zone-a"],
        "effective_from": "2026-01-01T00:00",
        "effective_to": "2026-10-04T21:59",
        "is_correction": False,
        "correction_reason": None,
    },
    {
        "version_id": "sv-n01-v2",
        "site_id": "N-01",
        "boundary_version": "v2",
        "catchment_zones": ["zone-a", "zone-c"],
        "effective_from": "2026-10-04T22:00",
        "effective_to": None,
        "is_correction": True,
        "correction_reason": "边界勘误：zone-c 实际汇入 N-01 街区",
    },
    {
        "version_id": "sv-n02-v1",
        "site_id": "N-02",
        "boundary_version": "v1",
        "catchment_zones": ["zone-b", "zone-c"],
        "effective_from": "2026-01-01T00:00",
        "effective_to": "2026-10-04T21:59",
        "is_correction": False,
        "correction_reason": None,
    },
    {
        "version_id": "sv-n02-v2",
        "site_id": "N-02",
        "boundary_version": "v2",
        "catchment_zones": ["zone-b"],
        "effective_from": "2026-10-04T22:00",
        "effective_to": None,
        "is_correction": True,
        "correction_reason": "边界勘误：zone-c 移交 N-01 街区",
    },
    {
        "version_id": "sv-n03-v1",
        "site_id": "N-03",
        "boundary_version": "v1",
        "catchment_zones": ["zone-d"],
        "effective_from": "2026-01-01T00:00",
        "effective_to": None,
        "is_correction": False,
        "correction_reason": None,
    },
]

# ---------------------------------------------------------------------------
# activities.json：活动时段版本与限时播放干预
# ---------------------------------------------------------------------------
ACTIVITIES = [
    {
        "activity_id": "mkt-01",
        "site_id": "N-01",
        "activity_type": "night_market",
        "version": "v1",
        "window_start": "21:30",
        "window_end": "02:30",
        "effective_from": "2026-10-01T00:00",
        "effective_to": "2026-10-02T21:59",
        "is_correction": True,
        "correction_reason": "商户报备时段更正",
    },
    {
        "activity_id": "mkt-01",
        "site_id": "N-01",
        "activity_type": "night_market",
        "version": "v2",
        "window_start": "22:00",
        "window_end": "02:00",
        "effective_from": "2026-10-02T22:00",
        "effective_to": None,
        "is_correction": False,
        "correction_reason": None,
    },
    {
        "activity_id": "mkt-02",
        "site_id": "N-02",
        "activity_type": "night_market",
        "version": "v1",
        "window_start": "22:00",
        "window_end": "01:00",
        "effective_from": "2026-10-01T00:00",
        "effective_to": None,
        "is_correction": False,
        "correction_reason": None,
    },
    {
        "activity_id": "ivn-01",
        "site_id": "N-01",
        "activity_type": "intervention",
        "version": "v1",
        "measure": "限时播放：户外音响 22:30 后停止播放",
        "window_start": "22:00",
        "window_end": "06:00",
        "effective_from": "2026-10-05T22:00",
        "effective_to": None,
        "is_correction": False,
        "correction_reason": None,
    },
]

# ---------------------------------------------------------------------------
# weather.json：逐地点逐夜气象，bucket 由 condition 决定
# ---------------------------------------------------------------------------
WEATHER_CONDITIONS = {
    ("N-01", "2026-10-02"): ("dry", "clear", 18, 3.1),
    ("N-01", "2026-10-03"): ("dry", "cloudy", 19, 2.4),
    ("N-01", "2026-10-04"): ("wet", "rain", 17, 4.8),
    ("N-01", "2026-10-05"): ("dry", "clear", 16, 2.0),
    ("N-01", "2026-10-06"): ("dry", "clear", 15, 1.8),
    ("N-02", "2026-10-02"): ("dry", "clear", 18, 2.8),
    ("N-02", "2026-10-03"): ("dry", "cloudy", 19, 2.2),
    ("N-02", "2026-10-04"): ("dry", "cloudy", 18, 2.6),
    ("N-02", "2026-10-05"): ("dry", "clear", 16, 1.9),
    ("N-02", "2026-10-06"): ("dry", "clear", 15, 1.7),
    ("N-03", "2026-10-02"): ("dry", "clear", 17, 3.3),
    ("N-03", "2026-10-03"): ("dry", "cloudy", 18, 2.9),
    ("N-03", "2026-10-04"): ("dry", "cloudy", 17, 2.5),
    ("N-03", "2026-10-05"): ("dry", "clear", 15, 2.1),
    ("N-03", "2026-10-06"): ("dry", "clear", 14, 1.6),
}

# ---------------------------------------------------------------------------
# 读数计划：每个夜间窗口 8 个槽位
# 元组 (quality, db)；X 表示该槽位无任何记录（未覆盖/缺失）
# V=valid  P=partial  M=maintenance  O=offline  X=无记录
# ---------------------------------------------------------------------------
READING_PLANS: dict[str, dict[str, list]] = {
    "N-01": {
        "2026-10-02": [("V", 70), ("V", 69), ("V", 71), ("V", 66), ("V", 64), ("V", 62), ("V", 60), ("V", 58)],
        # s-001 与 s-002 是资料包原有记录，位置与数值必须保持
        "2026-10-03": [("V", 68), ("X", None), ("O", None), ("V", 67), ("V", 63), ("V", 60), ("V", 57), ("V", 55)],
        "2026-10-04": [("V", 66), ("M", None), ("M", None), ("V", 64), ("V", 60), ("V", 58), ("V", 56), ("V", 54)],
        "2026-10-05": [("V", 62), ("V", 61), ("V", 60), ("P", 59), ("V", 57), ("V", 55), ("V", 53), ("V", 52)],
        "2026-10-06": [("V", 63), ("O", None), ("X", None), ("P", 60), ("X", None), ("V", 55), ("V", 53), ("V", 52)],
    },
    "N-02": {
        "2026-10-02": [("V", 60), ("V", 62), ("V", 61), ("V", 59), ("V", 57), ("V", 55), ("V", 53), ("V", 52)],
        # s-003 是资料包原有记录
        "2026-10-03": [("X", None), ("P", 51), ("V", 55), ("V", 56), ("V", 54), ("V", 52), ("V", 51), ("V", 50)],
        "2026-10-04": [("X", None), ("V", 57), ("V", 58), ("V", 56), ("V", 54), ("V", 52), ("V", 50), ("V", 49)],
        "2026-10-05": [("X", None), ("M", None), ("V", 56), ("V", 55), ("V", 53), ("V", 51), ("V", 50), ("V", 49)],
        "2026-10-06": [("X", None), ("X", None), ("V", 57), ("V", 59), ("V", 56), ("V", 54), ("V", 52), ("V", 50)],
    },
    "N-03": {
        "2026-10-02": [("X", None), ("V", 66), ("V", 67), ("V", 64), ("X", None), ("X", None), ("X", None), ("X", None)],
        "2026-10-03": [("X", None), ("P", 64), ("V", 65), ("O", None), ("X", None), ("X", None), ("X", None), ("X", None)],
        "2026-10-04": [("X", None)] * 8,
        "2026-10-05": [("X", None), ("V", 63), ("V", 66), ("V", 62), ("X", None), ("X", None), ("X", None), ("X", None)],
        "2026-10-06": [("X", None)] * 8,
    },
}

QUALITY_MAP = {"V": "valid", "P": "partial", "M": "maintenance", "O": "offline"}

# ---------------------------------------------------------------------------
# complaints.json：前三条为原始记录；含重复事件、分区更正归属、撤回
# ---------------------------------------------------------------------------
COMPLAINTS = [
    {"complaint_id": "c-001", "site_id": "N-01", "recorded_site_id": "N-01", "zone_id": "zone-a",
     "reported_at": "2026-10-03T22:20", "channel": "phone", "text_key": "music", "status": "received"},
    {"complaint_id": "c-002", "site_id": "N-01", "recorded_site_id": "N-01", "zone_id": "zone-a",
     "reported_at": "2026-10-03T22:44", "channel": "app", "text_key": "music", "status": "received"},
    {"complaint_id": "c-003", "site_id": "N-02", "recorded_site_id": "N-02", "zone_id": "zone-b",
     "reported_at": "2026-10-03T23:10", "channel": "app", "text_key": "unknown", "status": "received"},
    {"complaint_id": "c-004", "site_id": "N-01", "recorded_site_id": "N-01", "zone_id": "zone-a",
     "reported_at": "2026-10-02T23:05", "channel": "app", "text_key": "music", "status": "received"},
    # zone-c 在 v2 边界下归属 N-01，录入时仍记在 N-02
    {"complaint_id": "c-005", "site_id": "N-02", "recorded_site_id": "N-02", "zone_id": "zone-c",
     "reported_at": "2026-10-05T00:40", "channel": "phone", "text_key": "music", "status": "received"},
    {"complaint_id": "c-006", "site_id": "N-01", "recorded_site_id": "N-01", "zone_id": "zone-a",
     "reported_at": "2026-10-04T23:30", "channel": "app", "text_key": "loudspeaker", "status": "received"},
    {"complaint_id": "c-007", "site_id": "N-02", "recorded_site_id": "N-02", "zone_id": "zone-b",
     "reported_at": "2026-10-03T23:40", "channel": "phone", "text_key": "music", "status": "received"},
    {"complaint_id": "c-008", "site_id": "N-01", "recorded_site_id": "N-01", "zone_id": "zone-a",
     "reported_at": "2026-10-05T22:50", "channel": "app", "text_key": "music", "status": "received"},
    {"complaint_id": "c-009", "site_id": "N-01", "recorded_site_id": "N-01", "zone_id": "zone-a",
     "reported_at": "2026-10-05T23:05", "channel": "app", "text_key": "music", "status": "withdrawn"},
    {"complaint_id": "c-010", "site_id": "N-03", "recorded_site_id": "N-03", "zone_id": "zone-d",
     "reported_at": "2026-10-03T00:20", "channel": "phone", "text_key": "music", "status": "received"},
    {"complaint_id": "c-011", "site_id": "N-03", "recorded_site_id": "N-03", "zone_id": "zone-d",
     "reported_at": "2026-10-06T01:15", "channel": "app", "text_key": "music", "status": "received"},
    {"complaint_id": "c-012", "site_id": "N-02", "recorded_site_id": "N-02", "zone_id": "zone-b",
     "reported_at": "2026-10-05T23:50", "channel": "app", "text_key": "unknown", "status": "received"},
]

# ---------------------------------------------------------------------------
# operations.json：o-001/o-002 为原始记录（同一队伍同一时间，构成冲突示例）
# ---------------------------------------------------------------------------
OPERATIONS = [
    {"operation_id": "o-001", "site_id": "N-01", "kind": "patrol", "starts_at": "2026-10-04T00:00",
     "duration_minutes": 60, "staff": "team-a", "equipment": "e-101", "required_qualification": "night_patrol",
     "status": "confirmed", "linked_operation_id": None, "result": None, "basis_snapshot_id": "snap-20261003-1800"},
    {"operation_id": "o-002", "site_id": "N-02", "kind": "notice", "starts_at": "2026-10-04T00:00",
     "duration_minutes": 30, "staff": "team-a", "equipment": None, "required_qualification": "community_notice",
     "status": "proposed", "linked_operation_id": None, "result": None, "basis_snapshot_id": None},
    {"operation_id": "o-003", "site_id": "N-02", "kind": "patrol", "starts_at": "2026-10-03T23:30",
     "duration_minutes": 60, "staff": "team-b", "equipment": "e-102", "required_qualification": "night_patrol",
     "status": "confirmed", "linked_operation_id": None, "result": None, "basis_snapshot_id": "snap-20261003-1800"},
    {"operation_id": "o-004", "site_id": "N-01", "kind": "patrol", "starts_at": "2026-10-05T23:30",
     "duration_minutes": 60, "staff": "team-a", "equipment": "e-101", "required_qualification": "night_patrol",
     "status": "completed", "linked_operation_id": None,
     "result": {"observed_db": 61, "exceeded": False, "note": "现场低于阈值，商户配合"},
     "basis_snapshot_id": "snap-20261005-1800"},
    {"operation_id": "o-005", "site_id": "N-01", "kind": "notice", "starts_at": "2026-10-06T21:45",
     "duration_minutes": 30, "staff": "team-c", "equipment": None, "required_qualification": "community_notice",
     "status": "confirmed", "linked_operation_id": None, "result": None, "basis_snapshot_id": "snap-20261006-1800"},
    {"operation_id": "o-006", "site_id": "N-01", "kind": "revisit", "starts_at": "2026-10-07T22:30",
     "duration_minutes": 60, "staff": "team-a", "equipment": "e-101", "required_qualification": "night_patrol",
     "status": "confirmed", "linked_operation_id": "o-004", "result": None, "basis_snapshot_id": "snap-20261006-1800"},
    {"operation_id": "o-007", "site_id": "N-02", "kind": "revisit", "starts_at": "2026-10-07T23:30",
     "duration_minutes": 90, "staff": "team-b", "equipment": "e-102", "required_qualification": "night_patrol",
     "status": "confirmed", "linked_operation_id": "o-003", "result": None, "basis_snapshot_id": "snap-20261006-1800"},
    {"operation_id": "o-008", "site_id": "N-03", "kind": "patrol", "starts_at": "2026-10-07T00:30",
     "duration_minutes": 60, "staff": "team-b", "equipment": "e-102", "required_qualification": "night_patrol",
     "status": "proposed", "linked_operation_id": None, "result": None, "basis_snapshot_id": None},
]

RESOURCES = [
    {"resource_id": "team-a", "resource_type": "team", "qualifications": ["night_patrol", "community_notice"], "home_site": "N-01"},
    {"resource_id": "team-b", "resource_type": "team", "qualifications": ["night_patrol"], "home_site": "N-02"},
    {"resource_id": "team-c", "resource_type": "team", "qualifications": ["community_notice"], "home_site": "N-01"},
    {"resource_id": "e-101", "resource_type": "equipment", "qualifications": [], "home_site": "N-01"},
    {"resource_id": "e-102", "resource_type": "equipment", "qualifications": [], "home_site": "N-02"},
]

TRAVEL_MINUTES = {
    "N-01": {"N-01": 0, "N-02": 20, "N-03": 120},
    "N-02": {"N-01": 20, "N-02": 0, "N-03": 60},
    "N-03": {"N-01": 120, "N-02": 60, "N-03": 0},
}

POLICY = {
    "policy_id": "night-noise-policy",
    "version": "v1",
    "threshold_db": 65,
    "night_start": "22:00",
    "night_end": "06:00",
    "slot_minutes": 60,
    "cluster_window_minutes": 60,
    "maturity_min_valid_ratio": 0.75,
    "risk_missing_weight": 1.0,
    "risk_partial_weight": 0.5,
    "risk_maintenance_weight": 1.0,
    "risk_offline_weight": 1.0,
    "patrol_duration_minutes": 60,
    "notice_duration_minutes": 30,
    "notice_window_start": "21:30",
    "notice_window_end": "22:30",
    "band_high_upper": 0.75,
    "band_high_lower": 0.5,
    "band_moderate_upper": 0.25,
}


def build_sensors() -> list[dict]:
    records: list[dict] = [
        # 原始三条记录，字段与取值保持不变
        {"reading_id": "s-001", "site_id": "N-01", "sensor_id": "main", "observed_at": "2026-10-03T22:00", "db": 68, "quality": "valid"},
        {"reading_id": "s-002", "site_id": "N-01", "sensor_id": "main", "observed_at": "2026-10-03T23:00", "db": None, "quality": "offline"},
        {"reading_id": "s-003", "site_id": "N-02", "sensor_id": "main", "observed_at": "2026-10-03T23:00", "db": 51, "quality": "partial"},
    ]
    counter = 4
    for site_id in ("N-01", "N-02", "N-03"):
        for night in NIGHTS:
            for index, (mark, db) in enumerate(READING_PLANS[site_id][night]):
                if mark == "X":
                    continue  # 无记录就是无记录，绝不生成占位
                ts = slot_ts(night, index)
                # 跳过已作为原始记录写入的三条
                if (site_id, ts) in {("N-01", "2026-10-03T22:00"),
                                      ("N-01", "2026-10-03T23:00"),
                                      ("N-02", "2026-10-03T23:00")}:
                    continue
                records.append({
                    "reading_id": f"s-{counter:03d}",
                    "site_id": site_id,
                    "sensor_id": "main",
                    "observed_at": ts,
                    "db": db,
                    "quality": QUALITY_MAP[mark],
                })
                counter += 1
    return records


def build_weather() -> list[dict]:
    rows = []
    for i, (site_id, night) in enumerate(sorted(WEATHER_CONDITIONS), start=1):
        bucket, condition, temp_c, wind_ms = WEATHER_CONDITIONS[(site_id, night)]
        rows.append({
            "weather_id": f"w-{i:03d}",
            "site_id": site_id,
            "night_date": night,
            "bucket": bucket,
            "condition": condition,
            "temp_c": temp_c,
            "wind_ms": wind_ms,
        })
    return rows


def main() -> None:
    payloads = {
        "sites.json": SITES,
        "site_versions.json": SITE_VERSIONS,
        "activities.json": ACTIVITIES,
        "weather.json": build_weather(),
        "sensors.json": build_sensors(),
        "complaints.json": COMPLAINTS,
        "operations.json": OPERATIONS,
        "resources.json": RESOURCES,
        "travel_times.json": TRAVEL_MINUTES,
        "policy.json": POLICY,
    }
    for name, payload in payloads.items():
        path = DATA_DIR / name
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    print(f"wrote {len(payloads)} files to {DATA_DIR}")


if __name__ == "__main__":
    main()
