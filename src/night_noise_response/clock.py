"""夜间窗口与槽位的时间工具。

系统统一使用本地朴素时间（naive datetime），不做时区转换；
夜间窗口从 22:00 起，到次日 06:00 止，按固定分钟数切槽。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable


def parse_ts(value: str) -> datetime:
    """解析 ISO 本地时间字符串。"""
    return datetime.fromisoformat(value)


def parse_hm(value: str) -> tuple[int, int]:
    """解析 HH:MM。"""
    hour, minute = value.split(":")
    return int(hour), int(minute)


def night_key(dt: datetime, night_start_hour: int = 22) -> str:
    """返回时间点所属夜间窗口的起始日期（YYYY-MM-DD）。

    22:00 之前（含凌晨）归属前一天晚上开始的窗口，
    例如 2026-10-04T01:00 属于 2026-10-03 夜。
    """
    if dt.hour < night_start_hour:
        start_date = (dt - timedelta(days=1)).date()
    else:
        start_date = dt.date()
    return start_date.isoformat()


@dataclass(frozen=True)
class Window:
    """一个夜间观察窗口及其槽位时间点。"""

    night: str
    starts_at: datetime
    ends_at: datetime
    slot_minutes: int
    slots: tuple[datetime, ...]

    @property
    def slot_count(self) -> int:
        return len(self.slots)

    def slot_index(self, dt: datetime) -> int | None:
        """时间点落在哪个槽位；不在窗口内返回 None。"""
        if not (self.starts_at <= dt < self.ends_at):
            return None
        delta = dt - self.starts_at
        minutes = delta.total_seconds() / 60
        idx = int(minutes // self.slot_minutes)
        if 0 <= idx < self.slot_count:
            return idx
        return None

    def contains(self, dt: datetime) -> bool:
        return self.starts_at <= dt < self.ends_at


def build_window(night: str, night_start: str = "22:00",
                 night_end: str = "06:00", slot_minutes: int = 60) -> Window:
    """为某个起始日期构建夜间窗口。"""
    start_h, start_m = parse_hm(night_start)
    end_h, end_m = parse_hm(night_end)
    start_date = datetime.fromisoformat(night)
    starts_at = start_date.replace(hour=start_h, minute=start_m)
    # 夜间窗口跨午夜：结束钟点不晚于起始钟点即视为次日结束
    end_on_next_day = (end_h, end_m) <= (start_h, start_m)
    if end_on_next_day:
        ends_at = (starts_at + timedelta(days=1)).replace(
            hour=end_h, minute=end_m)
    else:
        ends_at = starts_at.replace(hour=end_h, minute=end_m)
    total_minutes = int((ends_at - starts_at).total_seconds() / 60)
    count = total_minutes // slot_minutes
    slots = tuple(
        starts_at + timedelta(minutes=slot_minutes * i) for i in range(count)
    )
    return Window(
        night=night,
        starts_at=starts_at,
        ends_at=ends_at,
        slot_minutes=slot_minutes,
        slots=slots,
    )


def build_windows(nights: Iterable[str], night_start: str = "22:00",
                  night_end: str = "06:00", slot_minutes: int = 60
                  ) -> dict[str, Window]:
    return {
        night: build_window(night, night_start, night_end, slot_minutes)
        for night in nights
    }


def overlap(start_a: datetime, end_a: datetime,
            start_b: datetime, end_b: datetime) -> bool:
    """两个半开区间是否重叠。"""
    return start_a < end_b and start_b < end_a
