from sqlalchemy import delete, select, func
from . import models as m


class Repository:
    model = None

    def __init__(self, db):
        self.db = db

    def get(self, id):
        return self.db.get(self.model, id)

    def add(self, **values):
        obj = self.model(**values)
        self.db.add(obj)
        self.db.flush()
        return obj

    def all(self, **filters):
        return list(self.db.scalars(select(self.model).filter_by(**filters)))


class Projects(Repository):
    model = m.Project

    def locked(self, id):
        return self.db.scalar(
            select(m.Project).where(m.Project.id == id).with_for_update()
        )

    def delete_tree(self, project_id):
        db = self.db
        for job in db.scalars(
            select(m.Job).where(
                m.Job.project_id == project_id,
                m.Job.status.in_(["queued", "running"]),
            )
        ):
            job.status, job.token = "cancelled", None
        db.flush()
        row = db.get(m.Project, project_id)
        row.current_plan_id = None
        db.flush()
        for model in (
            m.Alert,
            m.Assessment,
            m.Observation,
            m.Job,
            m.Binding,
            m.WorkState,
            m.Source,
            m.Audit,
            m.Plan,
        ):
            db.execute(delete(model).where(model.project_id == project_id))
        db.delete(row)


class Plans(Repository):
    model = m.Plan

    def next_version(self, project_id):
        return (
            self.db.scalar(
                select(func.max(m.Plan.version)).where(m.Plan.project_id == project_id)
            )
            or 0
        ) + 1

    def states(self, project_id):
        return {
            s.work_id: s.status
            for s in self.db.scalars(
                select(m.WorkState).where(m.WorkState.project_id == project_id)
            )
        }

    def set_state(self, project_id, work_id, status):
        from .db import now

        state = self.db.scalar(
            select(m.WorkState).filter_by(project_id=project_id, work_id=work_id)
        )
        if not state:
            state = m.WorkState(project_id=project_id, work_id=work_id, status=status)
            self.db.add(state)
        state.status, state.updated_at = status, now()


class Sources(Repository):
    model = m.Source


class Bindings(Repository):
    model = m.Binding

    def latest(self, source_id):
        return self.db.scalar(
            select(m.Binding)
            .where(m.Binding.source_id == source_id)
            .order_by(m.Binding.revision.desc())
            .limit(1)
        )


class Jobs(Repository):
    model = m.Job

    def active(self, source_id):
        return self.db.scalar(
            select(m.Job)
            .where(
                m.Job.source_id == source_id, m.Job.status.in_(["queued", "running"])
            )
            .limit(1)
        )


class Audits(Repository):
    model = m.Audit

    def record(self, project_id, action, entity_id, **data):
        return self.add(
            project_id=project_id, action=action, entity_id=entity_id, data=data
        )

    def recent(self, project_id, limit=200):
        return list(
            self.db.scalars(
                select(m.Audit)
                .where(m.Audit.project_id == project_id)
                .order_by(m.Audit.created_at.desc())
                .limit(limit)
            )
        )


class Observations(Repository):
    model = m.Observation

    def recent(self, source_id, limit=100):
        return list(
            self.db.scalars(
                select(m.Observation)
                .where(m.Observation.source_id == source_id)
                .order_by(m.Observation.created_at.desc())
                .limit(limit)
            )
        )


class Assessments(Repository):
    model = m.Assessment


class Alerts(Repository):
    model = m.Alert

    def recent(self, project_id, limit=None):
        return list(
            self.db.scalars(
                select(m.Alert)
                .where(m.Alert.project_id == project_id)
                .order_by(m.Alert.last_seen.desc())
                .limit(limit)
            )
        )
