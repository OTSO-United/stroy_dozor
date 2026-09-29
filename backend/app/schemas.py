from datetime import datetime
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from .planning_presets import PLANNING_CLASS_IDS


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(DTO):
    critical_after_seconds: int = Field(3600, ge=1, le=604800)
    critical_missing_ratio: float = Field(0.5, ge=0, le=1)
    warning_after_seconds: int = Field(0, ge=0, le=604800)
    absence_confirm_seconds: int = Field(30, ge=0, le=86400)
    count_change_confirm_seconds: int = Field(10, ge=0, le=3600)
    max_gap_seconds: float = Field(3, ge=1, le=3600)
    max_gap_frames: int = Field(3, ge=1, le=30)
    presence_grace_frames: int = Field(3, ge=0, le=30)
    idle_after_seconds: int = Field(300, ge=10, le=3600)
    motion_fraction_threshold: float = Field(0.008, ge=0.001, le=0.1)
    monitor_interval_seconds: int = Field(5, ge=1, le=300)
    equipment_activity: dict[str, Literal["mobile", "stationary_capable"]] = Field(
        default_factory=dict
    )


class ProjectLocation(DTO):
    project_type_id: str | None = Field(None, max_length=32)
    latitude: float | None = Field(None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(None, ge=-180, le=180, allow_inf_nan=False)

    @field_validator("project_type_id")
    @classmethod
    def empty_type(cls, value):
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def coordinate_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Укажите точку целиком: широту и долготу")
        return self


class ProjectInput(ProjectLocation):
    name: str = Field(min_length=1, max_length=200)
    address: str = Field("", max_length=500)
    timezone: str = "Europe/Moscow"
    class_ids: list[int] = Field(
        default_factory=lambda: list(PLANNING_CLASS_IDS),
        min_length=1,
        max_length=28,
    )

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, v):
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Неизвестный часовой пояс") from error
        return v

    @field_validator("class_ids")
    @classmethod
    def unique_classes(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("Классы должны быть уникальны")
        return v


class ProjectUpdate(ProjectLocation):
    name: str = Field(min_length=1, max_length=200)
    address: str = Field("", max_length=500)
    expected_revision: int


class DeleteProjectInput(DTO):
    expected_revision: int


class SettingsInput(Settings):
    expected_revision: int


class Work(DTO):
    id: str = Field(default_factory=lambda: str(uuid4()), max_length=36)
    code: str = Field(pattern=r"^\d+(\.\d+)*$", max_length=60)
    title: str = Field(min_length=1, max_length=500)
    starts_at: datetime
    ends_at: datetime
    resources: dict[str, int]

    @field_validator("starts_at", "ends_at")
    @classmethod
    def aware(cls, v):
        if v.tzinfo is None:
            raise ValueError("Укажите часовой пояс")
        return v

    @field_validator("resources")
    @classmethod
    def counts(cls, v):
        if any(n < 0 or n > 10000 for n in v.values()):
            raise ValueError("Количество: от 0 до 10000")
        return v

    @model_validator(mode="after")
    def interval(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("Окончание должно быть позже начала")
        return self


class PlanInput(DTO):
    works: list[Work] = Field(min_length=1, max_length=1000)
    base_plan_id: str | None = None
    additional_class_ids: list[int] = Field(default_factory=list, max_length=28)


class DeletePlanInput(DTO):
    expected_plan_id: str


class ShiftInput(DTO):
    work_id: str
    hours: float = Field(ge=-87600, le=87600)
    cascade: bool = True


class ShiftApply(ShiftInput):
    expected_plan_id: str


class StatusInput(DTO):
    status: Literal["planned", "in_progress", "completed"]
    note: str = Field("", max_length=2000)


class SourceInput(DTO):
    name: str = Field(min_length=1, max_length=200)
    uri: str = Field(min_length=1, max_length=2048)
    sample_seconds: float = Field(1, ge=1, le=3600)


class SourceConnection(DTO):
    uri: str


class SourceConfig(DTO):
    name: str | None = Field(None, min_length=1, max_length=200)
    uri: str | None = Field(None, min_length=1, max_length=2048)
    sample_seconds: float = Field(ge=1, le=3600)
    capture_start: datetime | None = None
    demo_loop_enabled: bool | None = None
    demo_loop_duration_seconds: float | None = Field(None, gt=0, allow_inf_nan=False)

    @field_validator("capture_start")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Укажите часовой пояс начала записи")
        return value


class Region(DTO):
    id: str = Field(default_factory=lambda: str(uuid4()), max_length=36)
    name: str = Field(min_length=1, max_length=200)
    work_ids: list[str] = Field(min_length=1, max_length=100)
    polygon: list[tuple[float, float]] = Field(min_length=3, max_length=100)
    primary: bool = True
    visibility_confirmed: bool = True
    color: str = Field("#27836b", pattern=r"^#[0-9a-fA-F]{6}$")
    visible: bool = True

    @field_validator("polygon")
    @classmethod
    def normalized(cls, points):
        if any(not (0 <= x <= 1 and 0 <= y <= 1) for x, y in points):
            raise ValueError("Координаты вне кадра")
        area = (
            abs(
                sum(
                    points[i][0] * points[(i + 1) % len(points)][1]
                    - points[(i + 1) % len(points)][0] * points[i][1]
                    for i in range(len(points))
                )
            )
            / 2
        )
        if area < 0.0001:
            raise ValueError("Зона имеет нулевую площадь")
        return points


class BindingInput(DTO):
    expected_revision: int = 0
    regions: list[Region] = Field(max_length=100)


class ReviewInput(DTO):
    decision: Literal["confirmed", "dismissed", "acknowledged"]
    note: str = Field("", max_length=2000)
