"""
Data schemas and conversions between Meross raw schedule payload and human-readable models.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator


DAYS_OF_WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MINUTES_PER_DAY = 1440
MAX_SECTIONS_PER_DAY = 8
MIN_TEMP_C = 5.0
MAX_TEMP_C = 35.0


def minutes_to_time_str(minutes: int) -> str:
    """Converts minutes from midnight (0..1440) to 'HH:MM' string (e.g. 1440 -> '24:00')."""
    if minutes < 0 or minutes > MINUTES_PER_DAY:
        raise ValueError(f"Minutes {minutes} out of daily range 0..1440")
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"


def time_str_to_minutes(time_str: str) -> int:
    """Converts 'HH:MM' string to minutes from midnight (0..1440)."""
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{time_str}', expected 'HH:MM'")
    hours = int(parts[0])
    mins = int(parts[1])
    total = hours * 60 + mins
    if total < 0 or total > MINUTES_PER_DAY:
        raise ValueError(f"Time '{time_str}' is out of bounds (00:00 - 24:00)")
    return total


class ThermostatSlot(BaseModel):
    """A single scheduled heating interval."""
    start_time: str = Field(..., description="Start time in HH:MM (e.g. '06:00')")
    end_time: str = Field(..., description="End time in HH:MM (e.g. '08:30')")
    temp: float = Field(..., ge=MIN_TEMP_C, le=MAX_TEMP_C, description="Target temperature in °C (e.g. 20.5)")

    @property
    def duration_minutes(self) -> int:
        return time_str_to_minutes(self.end_time) - time_str_to_minutes(self.start_time)

    @model_validator(mode="after")
    def validate_times(self) -> "ThermostatSlot":
        start_m = time_str_to_minutes(self.start_time)
        end_m = time_str_to_minutes(self.end_time)
        if end_m <= start_m:
            raise ValueError(f"end_time '{self.end_time}' must be strictly after start_time '{self.start_time}'")
        # Round temp to 1 decimal place
        self.temp = round(self.temp, 1)
        return self


class WeekSchedule(BaseModel):
    """Full 7-day schedule for a thermostat."""
    mon: List[ThermostatSlot]
    tue: List[ThermostatSlot]
    wed: List[ThermostatSlot]
    thu: List[ThermostatSlot]
    fri: List[ThermostatSlot]
    sat: List[ThermostatSlot]
    sun: List[ThermostatSlot]

    @model_validator(mode="after")
    def validate_all_days(self) -> "WeekSchedule":
        for day in DAYS_OF_WEEK:
            slots = getattr(self, day)
            if not slots:
                raise ValueError(f"Day '{day}' has no scheduled slots.")
            if len(slots) > MAX_SECTIONS_PER_DAY:
                raise ValueError(f"Day '{day}' exceeds maximum {MAX_SECTIONS_PER_DAY} slots ({len(slots)} provided)")

            # Check continuous coverage from 00:00 to 24:00
            if slots[0].start_time != "00:00":
                raise ValueError(f"Day '{day}' first slot must start at '00:00', got '{slots[0].start_time}'")
            if slots[-1].end_time != "24:00":
                raise ValueError(f"Day '{day}' last slot must end at '24:00', got '{slots[-1].end_time}'")

            for i in range(len(slots) - 1):
                if slots[i].end_time != slots[i + 1].start_time:
                    raise ValueError(
                        f"Day '{day}' gap or overlap between slot {i} (ends {slots[i].end_time}) and slot {i+1} (starts {slots[i+1].start_time})"
                    )

            total_dur = sum(s.duration_minutes for s in slots)
            if total_dur != MINUTES_PER_DAY:
                raise ValueError(f"Day '{day}' total duration must equal {MINUTES_PER_DAY} minutes, got {total_dur}")
        return self


class SetModeRequest(BaseModel):
    mode: int = Field(..., description="Thermostat mode: 3 for Schedule (Auto), 4 for Manual")


class SetTargetTempRequest(BaseModel):
    target_temp: float = Field(..., ge=MIN_TEMP_C, le=MAX_TEMP_C, description="Target temperature in °C")


class DeviceSummary(BaseModel):
    """High level overview of a thermostat device."""
    uuid: str
    name: str
    model: str
    online: bool
    current_temp: Optional[float] = None
    target_temp: Optional[float] = None
    mode: Optional[int] = None
    is_heating: Optional[bool] = None
    heat_temp: Optional[float] = None
    cool_temp: Optional[float] = None
    eco_temp: Optional[float] = None
    manual_temp: Optional[float] = None


class DeviceScheduleResponse(BaseModel):
    """Complete device detail and its schedule."""
    device: DeviceSummary
    schedule: WeekSchedule
    raw_schedule: Optional[Dict[str, Any]] = None


# Conversion functions
def raw_day_to_slots(raw_day_list: List[List[int]]) -> List[ThermostatSlot]:
    """
    Converts raw day pairs e.g. [[375, 175], [105, 210], ...]
    into human-readable ThermostatSlot models.
    """
    slots: List[ThermostatSlot] = []
    current_minutes = 0
    for dur, temp_x10 in raw_day_list:
        start_time = minutes_to_time_str(current_minutes)
        current_minutes += dur
        end_time = minutes_to_time_str(current_minutes)
        temp_c = round(temp_x10 / 10.0, 1)
        slots.append(ThermostatSlot(start_time=start_time, end_time=end_time, temp=temp_c))
    return slots


def slots_to_raw_day(slots: List[ThermostatSlot]) -> List[List[int]]:
    """
    Converts human-readable ThermostatSlot models into raw day pairs
    e.g. [[375, 175], [105, 210], ...]
    """
    raw_list: List[List[int]] = []
    for slot in slots:
        dur = slot.duration_minutes
        temp_x10 = int(round(slot.temp * 10))
        raw_list.append([dur, temp_x10])
    return raw_list


def raw_payload_to_week_schedule(payload: Dict[str, Any]) -> WeekSchedule:
    """
    Parses the schedule payload returned from Meross:
    {"schedule": [{"channel": 0, "section": 8, "sun": [...], "mon": [...]}]}
    """
    schedule_list = payload.get("schedule", [])
    if not schedule_list:
        raise ValueError("Invalid payload: missing 'schedule' array")
    sched_obj = schedule_list[0]

    day_kwargs = {}
    for day in DAYS_OF_WEEK:
        raw_day = sched_obj.get(day, [])
        if not raw_day:
            raise ValueError(f"Missing day '{day}' in raw schedule payload")
        day_kwargs[day] = raw_day_to_slots(raw_day)

    return WeekSchedule(**day_kwargs)


def week_schedule_to_raw_payload(schedule: WeekSchedule, channel: int = 0, section: int = 8) -> Dict[str, Any]:
    """
    Serializes a WeekSchedule back to the raw payload expected by Meross SET command:
    {"schedule": [{"channel": channel, "section": section, "sun": [...], ...}]}
    """
    sched_obj: Dict[str, Any] = {
        "channel": channel,
        "section": section,
    }
    for day in DAYS_OF_WEEK:
        slots = getattr(schedule, day)
        sched_obj[day] = slots_to_raw_day(slots)

    return {"schedule": [sched_obj]}
