from datetime import timedelta
from sqlalchemy import select, or_, and_, update
from .models import Job
from .db import now, uid
from .config import LEASE_SECONDS


def claim(db, job_model=Job):
    Job = job_model
    instant = now()
    row = db.scalar(
        select(Job)
        .where(
            or_(
                and_(
                    Job.status == "queued",
                    or_(Job.lease_until.is_(None), Job.lease_until <= instant),
                ),
                and_(Job.status == "running", Job.lease_until < instant),
            ),
            Job.attempts < 3,
        )
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not row:
        return None
    old_token = row.token
    token = uid()
    condition = Job.token.is_(None) if old_token is None else Job.token == old_token
    updated = db.execute(
        update(Job)
        .where(Job.id == row.id, condition, Job.status.in_(["queued", "running"]))
        .values(
            status="running",
            token=token,
            attempts=Job.attempts + 1,
            lease_until=instant + timedelta(seconds=LEASE_SECONDS),
            updated_at=instant,
            **(
                {
                    "payload": {
                        **row.payload,
                        "started_at": row.payload.get("started_at")
                        or instant.isoformat(),
                    }
                }
                if hasattr(row, "payload")
                else {}
            ),
        )
    )
    if updated.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    db.refresh(row)
    return row


def fenced(db, job_id, token, *, job_model=Job, **values):
    Job = job_model
    instant = now()
    result = db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.token == token,
            Job.status == "running",
            Job.lease_until > instant,
        )
        .values(
            updated_at=instant,
            lease_until=instant + timedelta(seconds=LEASE_SECONDS),
            **values,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise LeaseLost("Lease истёк или задача отменена")


class LeaseLost(RuntimeError):
    pass
