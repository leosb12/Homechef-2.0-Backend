from datetime import datetime
import re
from zoneinfo import ZoneInfo


DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_LABELS = {
    "monday": "Lunes",
    "tuesday": "Martes",
    "wednesday": "Miércoles",
    "thursday": "Jueves",
    "friday": "Viernes",
    "saturday": "Sábado",
    "sunday": "Domingo",
}
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}$")


def normalize_weekly_schedule(raw_schedule):
    if not isinstance(raw_schedule, list):
        raise ValueError("El horario semanal debe enviarse como una lista.")

    grouped = {day: [] for day in DAY_ORDER}
    for raw_item in raw_schedule:
        if not isinstance(raw_item, dict):
            raise ValueError("Cada horario debe tener día, hora de inicio y hora de fin.")
        day = str(raw_item.get("day", "")).strip().lower()
        if day not in grouped:
            raise ValueError("Selecciona un día válido para cada horario.")

        enabled = bool(raw_item.get("enabled", True))
        start_time = str(raw_item.get("start_time", "")).strip()
        end_time = str(raw_item.get("end_time", "")).strip()
        modes = raw_item.get("modes", [])
        if isinstance(modes, str):
            modes = [modes]
        modes = [str(mode).strip().lower() for mode in modes if str(mode).strip().lower() in {"delivery", "pickup"}]

        if not enabled:
            continue
        _validate_time_range(start_time, end_time)
        if not modes:
            raise ValueError("Cada horario activo debe permitir delivery, retiro o ambas modalidades.")

        grouped[day].append(
            {
                "day": day,
                "enabled": True,
                "start_time": start_time,
                "end_time": end_time,
                "modes": sorted(set(modes)),
            }
        )

    normalized = []
    for day in DAY_ORDER:
        day_items = sorted(grouped[day], key=lambda item: item["start_time"])
        _validate_no_overlaps(day_items)
        normalized.extend(day_items)
    return normalized


def availability_summary(availability):
    weekly_schedule = availability.get("weekly_schedule", []) if isinstance(availability, dict) else []
    if not weekly_schedule:
        return "Sin horarios configurados"

    parts = []
    for item in weekly_schedule:
        label = DAY_LABELS.get(item.get("day"), item.get("day", ""))
        modes = item.get("modes", [])
        modes_label = " y ".join(["delivery" if mode == "delivery" else "retiro" for mode in modes])
        parts.append(f"{label} {item.get('start_time')} - {item.get('end_time')} ({modes_label})")
    return "; ".join(parts)


def is_open_now(availability, now=None):
    if not availability:
        return True
    if not availability.get("is_active", True):
        return False

    weekly_schedule = availability.get("weekly_schedule") or []
    if not weekly_schedule:
        return False

    current = now or datetime.now(ZoneInfo("America/La_Paz"))
    day = DAY_ORDER[current.weekday()]
    current_time = current.strftime("%H:%M")
    return any(
        item.get("enabled", True)
        and item.get("day") == day
        and item.get("start_time") <= current_time < item.get("end_time")
        for item in weekly_schedule
    )


def _validate_time_range(start_time, end_time):
    if not TIME_PATTERN.match(start_time) or not TIME_PATTERN.match(end_time):
        raise ValueError("Los horarios deben tener formato HH:MM.")
    start_minutes = _minutes(start_time)
    end_minutes = _minutes(end_time)
    if start_minutes is None or end_minutes is None:
        raise ValueError("Los horarios deben tener horas y minutos válidos.")
    if end_minutes <= start_minutes:
        raise ValueError("La hora de fin debe ser posterior a la hora de inicio.")


def _validate_no_overlaps(items):
    previous_end = None
    for item in items:
        start = _minutes(item["start_time"])
        end = _minutes(item["end_time"])
        if previous_end is not None and start < previous_end:
            raise ValueError(f"Hay horarios superpuestos para {DAY_LABELS[item['day']]}.")
        previous_end = end


def _minutes(value):
    try:
        hour, minute = [int(part) for part in value.split(":")]
    except (TypeError, ValueError):
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute
