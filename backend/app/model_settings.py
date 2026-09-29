"""Durable model post-processing settings outside the database migration chain."""

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from .vision import normalize_inference_parameters

PRODUCT_MODE_NAME = "Фоновый мониторинг объектов"
SETTINGS_FILE = "settings/model-inference.json"
_lock = threading.Lock()


class SettingsConflict(ValueError):
    pass


def _defaults(status):
    return normalize_inference_parameters(status.get("inference_defaults") or {})


def _model_identity(status):
    name = status.get("name")
    sha256 = status.get("sha256")
    if not isinstance(name, str) or not isinstance(sha256, str):
        return None
    return {"name": name, "sha256": sha256}


def _path(root):
    return Path(root) / SETTINGS_FILE


def _read(root, status):
    defaults = _defaults(status)
    model = _model_identity(status)
    path = _path(root)
    if not path.is_file():
        return {
            "version": 2,
            "revision": 0,
            "parameters": defaults,
            "updated_at": None,
            "model": model,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") not in (1, 2) or not isinstance(
            data.get("revision"), int
        ):
            raise ValueError
        if model is not None and data.get("model") != model:
            return {
                "version": 2,
                "revision": data["revision"] + 1,
                "parameters": defaults,
                "updated_at": None,
                "model": model,
            }
        data["parameters"] = normalize_inference_parameters(
            data.get("parameters") or {}, defaults
        )
        data["model"] = model
        return data
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError("Файл настроек модели повреждён") from error


def public_settings(root, status):
    data = _read(root, status)
    return {
        "product_mode_name": PRODUCT_MODE_NAME,
        "defaults": _defaults(status),
        "product": {
            "parameters": data["parameters"],
            "revision": data["revision"],
            "updated_at": data.get("updated_at"),
        },
    }


def product_parameters(root, status):
    return public_settings(root, status)["product"]["parameters"]


def update_product_settings(
    root, status, parameters, expected_revision, idempotency_key
):
    request = {
        "parameters": normalize_inference_parameters(parameters, _defaults(status)),
        "expected_revision": expected_revision,
    }
    request_hash = hashlib.sha256(
        json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with _lock:
        data = _read(root, status)
        if data.get("last_idempotency_key") == idempotency_key:
            if data.get("last_request_hash") != request_hash:
                raise SettingsConflict("Ключ уже использован с другими настройками")
            return public_settings(root, status)
        if data["revision"] != expected_revision:
            raise SettingsConflict("Настройки уже изменились; обновите страницу")
        if data["parameters"] == request["parameters"]:
            raise ValueError("Изменений настроек нет")
        updated = {
            "version": 2,
            "revision": data["revision"] + 1,
            "parameters": request["parameters"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "model": _model_identity(status),
            "last_idempotency_key": idempotency_key,
            "last_request_hash": request_hash,
        }
        path = _path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return public_settings(root, status)
