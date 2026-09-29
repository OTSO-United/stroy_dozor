import json
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import IntegrityError
from . import models as m
from .security import digest


def require(value, message="Запись не найдена"):
    if value is None:
        raise HTTPException(404, message)
    return value


def obj(row):
    return jsonable_encoder(
        {column.key: getattr(row, column.key) for column in row.__table__.columns}
    )


def mutation(db, scope, key, payload, operation):
    if not key or len(key) > 100:
        raise HTTPException(422, "Нужен Idempotency-Key длиной до 100 символов")
    hashed = digest(
        json.dumps(
            jsonable_encoder(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    cached = db.get(m.Idempotency, (scope, key))
    if cached:
        if cached.request_hash != hashed:
            raise HTTPException(409, "Ключ уже использован с другими данными")
        return cached.response
    try:
        result = jsonable_encoder(operation())
        db.add(
            m.Idempotency(scope=scope, key=key, request_hash=hashed, response=result)
        )
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        cached = db.get(m.Idempotency, (scope, key))
        if cached and cached.request_hash == hashed:
            return cached.response
        raise HTTPException(409, "Конфликт параллельного изменения, обновите страницу")
