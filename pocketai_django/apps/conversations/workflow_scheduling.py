from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone
from django.utils.dateparse import parse_datetime


class CronScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class CronSchedule:
    expression: str
    timezone: str = "UTC"
    start_at: datetime | None = None


def normalize_cron_schedule(trigger_config: object) -> CronSchedule:
    config = dict(trigger_config) if isinstance(trigger_config, Mapping) else {}

    expression = str(config.get("cron") or config.get("expression") or "").strip()
    tz_name = str(config.get("timezone") or config.get("tz") or "UTC").strip() or "UTC"

    start_at_raw = config.get("startAt") or config.get("start_at")
    start_at = None
    if start_at_raw:
        parsed = parse_datetime(str(start_at_raw).strip())
        if parsed is not None:
            start_at = parsed
            if timezone.is_naive(start_at):
                start_at = timezone.make_aware(start_at, timezone=dt_timezone.utc)

    return CronSchedule(expression=expression, timezone=tz_name, start_at=start_at)


def _parse_cron_field(field: str, *, minimum: int, maximum: int) -> set[int]:
    """
    Parse a single cron field into a set of allowed integers.

    Supported syntax (V1):
    - "*" (wildcard)
    - "*/n" (step)
    - "a-b" (range)
    - "a-b/n" (range with step)
    - "a,b,c" (lists of the above)
    - "n" (single integer)
    """

    field = (field or "").strip()
    if not field:
        raise CronScheduleError("Cron field cannot be blank.")

    if field == "*":
        return set(range(int(minimum), int(maximum) + 1))

    allowed: set[int] = set()
    for token in field.split(","):
        token = token.strip()
        if not token:
            raise CronScheduleError("Cron field contains an empty list item.")

        step = 1
        base = token
        if "/" in token:
            base, step_raw = token.split("/", 1)
            try:
                step = int(step_raw)
            except (TypeError, ValueError) as exc:
                raise CronScheduleError(f"Invalid step '{step_raw}'.") from exc
            if step <= 0:
                raise CronScheduleError("Cron step must be a positive integer.")

        if base == "*":
            start = int(minimum)
            end = int(maximum)
        elif "-" in base:
            start_raw, end_raw = base.split("-", 1)
            try:
                start = int(start_raw)
                end = int(end_raw)
            except (TypeError, ValueError) as exc:
                raise CronScheduleError(f"Invalid range '{base}'.") from exc
        else:
            try:
                start = int(base)
                end = int(base)
            except (TypeError, ValueError) as exc:
                raise CronScheduleError(f"Invalid value '{base}'.") from exc

        if start < int(minimum) or end > int(maximum) or start > end:
            raise CronScheduleError(f"Cron values must be between {minimum} and {maximum}.")

        for value in range(int(start), int(end) + 1, int(step)):
            allowed.add(int(value))

    if not allowed:
        raise CronScheduleError("Cron field resolved to no allowed values.")
    return allowed


def _load_zoneinfo(tz_name: str) -> ZoneInfo:
    name = (tz_name or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def compute_next_cron_trigger_at(schedule: CronSchedule, *, after: datetime | None = None) -> datetime:
    """
    Compute the next trigger time (UTC) for a supported cron schedule.

    V1 intentionally supports minute/hour expressions with dom/month/dow fixed as '*'
    to avoid a heavy dependency while still covering common "daily at 8am" needs.
    """

    expression = (schedule.expression or "").strip()
    if not expression:
        raise CronScheduleError("Cron schedule requires trigger_config.cron.")

    parts = expression.split()
    if len(parts) != 5:
        raise CronScheduleError("Cron expression must have 5 parts: minute hour dom month dow.")

    minute_field, hour_field, dom_field, month_field, dow_field = parts
    if dom_field != "*" or month_field != "*":
        raise CronScheduleError("Cron V1 supports minute/hour schedules with optional day-of-week only (dom/month must be '*').")

    minutes = sorted(_parse_cron_field(minute_field, minimum=0, maximum=59))
    hours = sorted(_parse_cron_field(hour_field, minimum=0, maximum=23))
    cron_dows = _parse_cron_field(dow_field, minimum=0, maximum=7) if dow_field != "*" else set(range(0, 8))
    normalized_dows = {0 if value == 7 else int(value) for value in cron_dows}
    if not minutes or not hours:
        raise CronScheduleError("Cron schedule resolved to an empty set of times.")

    tz = _load_zoneinfo(schedule.timezone)
    reference = after or timezone.now()
    if timezone.is_naive(reference):
        reference = timezone.make_aware(reference, timezone=dt_timezone.utc)

    if schedule.start_at and schedule.start_at > reference:
        reference = schedule.start_at

    local_ref = reference.astimezone(tz)
    local_start = local_ref.replace(second=0, microsecond=0)
    if local_start <= local_ref:
        local_start += timedelta(minutes=1)

    base_date = local_start.date()
    base_hour = int(local_start.hour)
    base_minute = int(local_start.minute)

    for day_offset in range(0, 367):
        day = base_date + timedelta(days=day_offset)
        cron_dow = (day.weekday() + 1) % 7
        if cron_dow not in normalized_dows:
            continue
        min_hour = base_hour if day_offset == 0 else 0
        min_minute = base_minute if day_offset == 0 else 0

        for hour in hours:
            if hour < min_hour:
                continue
            if hour == min_hour:
                idx = bisect_left(minutes, min_minute)
                if idx >= len(minutes):
                    continue
                minute = minutes[idx]
            else:
                minute = minutes[0]

            candidate_local = datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=tz)
            if candidate_local < local_start:
                continue
            return candidate_local.astimezone(dt_timezone.utc)

    raise CronScheduleError("Unable to find next cron trigger within 366 days.")


def compute_next_workflow_schedule_at(trigger_type: str, trigger_config: object, *, after: datetime | None = None) -> datetime | None:
    trigger_type = str(trigger_type or "").strip().lower()
    if trigger_type != "cron":
        return None
    schedule = normalize_cron_schedule(trigger_config)
    return compute_next_cron_trigger_at(schedule, after=after)
