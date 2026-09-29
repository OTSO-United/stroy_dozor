"""User-triggered address lookup; project persistence never depends on it."""

import json
import math
import os
import threading
import time
from collections import OrderedDict
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/v1/map", tags=["Map"])
REVERSE_URL = os.getenv("GEOCODER_REVERSE_URL", "https://photon.komoot.io/reverse")
SEARCH_URL = os.getenv("GEOCODER_SEARCH_URL", "https://photon.komoot.io/api")
_lock = threading.Lock()
_next_request = 0.0
_cache = OrderedDict()


def _request_features(url, query):
    request = Request(
        url + "?" + urlencode(query),
        headers={
            "User-Agent": "StroyKontur/0.1 (+https://github.com/OTSO-United/stroikontur-source)",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=8) as response:
        raw = response.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError("Geocoder response too large")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise ValueError("Invalid geocoder response")
    return payload["features"]


def _feature_address(feature):
    if not isinstance(feature, dict):
        raise ValueError("Invalid geocoder feature")
    properties = feature.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("Invalid geocoder properties")
    street = " ".join(
        str(properties.get(k) or "") for k in ("street", "housenumber")
    ).strip()
    parts = [
        properties.get("city") or properties.get("town") or properties.get("village"),
        properties.get("district"),
        street or properties.get("name"),
    ]
    return ", ".join(dict.fromkeys(str(p) for p in parts if p))[:500] or None


def request_address(latitude, longitude):
    features = _request_features(
        REVERSE_URL,
        dict(lat=latitude, lon=longitude, limit=1, radius=0.2),
    )
    if features and not isinstance(features[0], dict):
        raise ValueError("Invalid geocoder feature")
    return _feature_address(features[0]) if features else None


def _location(feature):
    if not isinstance(feature, dict):
        raise ValueError("Invalid geocoder feature")
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise ValueError("Invalid geocoder geometry")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ValueError("Invalid geocoder coordinates")
    longitude, latitude = map(float, coordinates[:2])
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        raise ValueError("Invalid geocoder coordinates")
    return {
        "latitude": round(latitude, 6),
        "longitude": round(longitude, 6),
        "address": _feature_address(feature),
    }


def request_locations(address):
    features = _request_features(SEARCH_URL, dict(q=address, limit=6, lang="ru"))
    return [_location(feature) for feature in features]


def request_location(address):
    return next(iter(request_locations(address)), None)


@router.get("/reverse")
def reverse(
    latitude: float = Query(..., ge=-90, le=90, allow_inf_nan=False),
    longitude: float = Query(..., ge=-180, le=180, allow_inf_nan=False),
):
    global _next_request
    point = ("reverse", round(latitude, 6), round(longitude, 6))
    # This API runs as a single process. Parallel replicas require a shared limiter.
    if not _lock.acquire(blocking=False):
        raise HTTPException(
            429,
            "Определение адреса занято. Повторите через секунду.",
            headers={"Retry-After": "1"},
        )
    try:
        cached = _cache.get(point)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(point)
            return cached[1]
        if not REVERSE_URL:
            raise HTTPException(503, "Поиск адреса отключён; введите адрес вручную")
        if time.monotonic() < _next_request:
            raise HTTPException(
                429,
                "Повторите поиск адреса через секунду",
                headers={"Retry-After": "1"},
            )
        _next_request = time.monotonic() + 1.1
        try:
            address = request_address(*point[1:])
        except (OSError, ValueError, TypeError, KeyError):
            raise HTTPException(503, "Адрес сейчас недоступен; точку можно сохранить")
        result = {
            "address": address,
            "provider": "Photon / OpenStreetMap",
            "approximate": True,
        }
        _cache[point] = (time.monotonic() + 86400, result)
        if len(_cache) > 1024:
            _cache.popitem(last=False)
        return result
    finally:
        _lock.release()


@router.get("/search")
def search(address: str = Query(..., min_length=3, max_length=500)):
    global _next_request
    normalized = " ".join(address.split())
    if len(normalized) < 3:
        raise HTTPException(422, "Введите не менее трёх символов адреса")
    key = ("search", normalized.casefold())
    if not _lock.acquire(blocking=False):
        raise HTTPException(
            429,
            "Поиск адреса занят. Повторите через секунду.",
            headers={"Retry-After": "1"},
        )
    try:
        cached = _cache.get(key)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(key)
            return cached[1]
        if not SEARCH_URL:
            raise HTTPException(503, "Поиск адреса отключён")
        if time.monotonic() < _next_request:
            raise HTTPException(
                429,
                "Повторите поиск адреса через секунду",
                headers={"Retry-After": "1"},
            )
        _next_request = time.monotonic() + 1.1
        try:
            locations = request_locations(normalized)
        except (OSError, ValueError, TypeError, KeyError):
            raise HTTPException(503, "Адрес сейчас недоступен")
        result = {
            **(
                locations[0]
                if locations
                else {"latitude": None, "longitude": None, "address": None}
            ),
            "suggestions": locations,
            "provider": "Photon / OpenStreetMap",
            "approximate": True,
        }
        _cache[key] = (time.monotonic() + 86400, result)
        if len(_cache) > 1024:
            _cache.popitem(last=False)
        return result
    finally:
        _lock.release()
