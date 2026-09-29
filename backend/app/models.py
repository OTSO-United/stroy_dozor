from datetime import datetime
from sqlalchemy import (
    String,
    JSON,
    Integer,
    Float,
    Boolean,
    ForeignKey,
    UniqueConstraint,
    Index,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base, uid, now, UTCDateTime


class ProjectType(Base):
    __tablename__ = "project_types"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class CatalogWork(Base):
    __tablename__ = "catalog_works"
    code: Mapped[str] = mapped_column(String(60), primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    source_row: Mapped[int] = mapped_column(Integer)


class ProjectTypeWork(Base):
    __tablename__ = "project_type_works"
    project_type_id: Mapped[str] = mapped_column(
        ForeignKey("project_types.id"), primary_key=True
    )
    work_code: Mapped[str] = mapped_column(
        ForeignKey("catalog_works.code"), primary_key=True
    )


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(500), default="")
    project_type_id: Mapped[str | None] = mapped_column(
        ForeignKey("project_types.id"), nullable=True, index=True
    )
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="Europe/Moscow")
    class_ids: Mapped[list] = mapped_column(JSON)
    settings: Mapped[dict] = mapped_column(JSON)
    current_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="approved")
    works: Mapped[list] = mapped_column(JSON)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    __table_args__ = (UniqueConstraint("project_id", "version"),)


class WorkState(Base):
    __tablename__ = "work_states"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    work_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default="planned")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    __table_args__ = (UniqueConstraint("project_id", "work_id"),)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    file_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    uri_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    capture_start: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    sample_seconds: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Binding(Base):
    __tablename__ = "bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    revision: Mapped[int] = mapped_column(Integer)
    regions: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    __table_args__ = (UniqueConstraint("source_id", "revision"),)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    __table_args__ = (Index("ix_jobs_claim", "status", "created_at"),)


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    sample_index: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    offset_seconds: Mapped[float] = mapped_column(Float)
    detections: Mapped[list] = mapped_column(JSON)
    quality: Mapped[dict] = mapped_column(JSON)
    model: Mapped[dict] = mapped_column(JSON)
    evidence_key: Mapped[str] = mapped_column(String(200))
    assessed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    __table_args__ = (UniqueConstraint("run_id", "sample_index"),)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"))
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    binding_id: Mapped[str | None] = mapped_column(
        ForeignKey("bindings.id"), nullable=True
    )
    results: Mapped[list] = mapped_column(JSON)
    settings: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    __table_args__ = (UniqueConstraint("observation_id"),)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    run_id: Mapped[str] = mapped_column(String(36))
    work_id: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(250), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open")
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime())
    duration_seconds: Mapped[float] = mapped_column(Float, default=0)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id"))
    details: Mapped[dict] = mapped_column(JSON)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)


class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(36))
    data: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(100), default="local-user")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Idempotency(Base):
    __tablename__ = "idempotency"
    scope: Mapped[str] = mapped_column(String(220), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class DetectorRun(Base):
    """Independent detector experiment; never an observation of a project."""

    __tablename__ = "detector_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    file_key: Mapped[str] = mapped_column(String(200))
    input_sha256: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    start_seconds: Mapped[float] = mapped_column(Float, default=0)
    end_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_seconds: Mapped[float] = mapped_column(Float, default=1)
    requested_model_sha: Mapped[str] = mapped_column(String(64))
    model: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    processed_frames: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class DetectorFrame(Base):
    __tablename__ = "detector_frames"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("detector_runs.id"), index=True)
    sample_index: Mapped[int] = mapped_column(Integer)
    offset_seconds: Mapped[float] = mapped_column(Float)
    detections: Mapped[list] = mapped_column(JSON)
    quality: Mapped[dict] = mapped_column(JSON)
    image_key: Mapped[str] = mapped_column(String(250))
    __table_args__ = (UniqueConstraint("run_id", "sample_index"),)
