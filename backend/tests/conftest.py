import sys
from pathlib import Path
import pytest
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, api, worker, monitor, security, detector_api, detector_worker
from app.db import Base


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    engine = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    from app.project_catalog import seed_catalog

    with factory() as db_seed:
        seed_catalog(db_seed)
        db_seed.commit()
    for name in ("media", "previews", "evidence", "reports"):
        (tmp_path / name).mkdir()
    for module in (api, worker, security, detector_api):
        monkeypatch.setattr(module, "DATA", tmp_path)
    monkeypatch.setattr(worker, "Session", factory)
    monkeypatch.setattr(monitor, "Session", factory)
    monkeypatch.setattr(detector_worker, "Session", factory)
    monkeypatch.setattr(detector_worker, "DATA", tmp_path)
    monkeypatch.setattr(
        api,
        "model_status",
        lambda: {
            "ready": False,
            "reason": "Модель не подключена",
            "supported_classes": [],
        },
    )

    def sessions():
        with factory() as session:
            yield session

    api.app.dependency_overrides[db.session] = sessions
    yield factory, tmp_path
    api.app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client(runtime):
    with TestClient(api.app) as client:
        yield client


@pytest.fixture
def project(client):
    return client.post(
        "/api/v1/projects",
        json={"name": "Тестовая стройка", "class_ids": [0, 1, 16]},
        headers={"Idempotency-Key": "create-project"},
    ).json()
