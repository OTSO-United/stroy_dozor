from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator, DateTime
from .config import DATABASE_URL, prepare_storage


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


def utc(value):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Timestamp must include a timezone")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        return utc(value) if value is not None else None


def make_engine(url):
    engine = create_engine(
        url,
        pool_pre_ping=True,
        **(
            {"connect_args": {"check_same_thread": False, "timeout": 30}}
            if url.startswith("sqlite")
            else {}
        ),
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    return engine


prepare_storage()
engine = make_engine(DATABASE_URL)
Session = sessionmaker(engine, expire_on_commit=False)


def session():
    with Session() as db:
        yield db
