import pytest
from io import BytesIO
from app import geocoding


def test_reverse_cache_limits_and_project_persistence(client, monkeypatch):
    geocoding._cache.clear()
    monkeypatch.setattr(geocoding, "_next_request", 0)
    calls = []

    def lookup(lat, lon):
        calls.append((lat, lon))
        return "Москва, Тестовая улица, 7"

    monkeypatch.setattr(geocoding, "request_address", lookup)
    url = "/api/v1/map/reverse?latitude=55.7&longitude=37.6"
    assert client.get(url).json()["address"] == "Москва, Тестовая улица, 7"
    assert client.get(url).status_code == 200
    assert len(calls) == 1
    assert client.get(url.replace("55.7", "55.8")).status_code == 429
    data = dict(
        name="Проверка точки",
        project_type_id="housing",
        latitude=55.7,
        longitude=37.6,
        address=client.get(url).json()["address"],
    )
    project = client.post(
        "/api/v1/projects", json=data, headers={"Idempotency-Key": "point"}
    )
    assert project.status_code == 201, project.text
    saved = client.get(f"/api/v1/projects/{project.json()['id']}").json()
    assert all(saved[k] == v for k, v in data.items())


@pytest.mark.parametrize("value", ["nan", "91", "inf"])
def test_reverse_rejects_invalid_coordinate(client, value):
    assert (
        client.get(f"/api/v1/map/reverse?latitude={value}&longitude=37").status_code
        == 422
    )


def test_reverse_unavailable_does_not_prevent_coordinate_only_save(client, monkeypatch):
    geocoding._cache.clear()
    monkeypatch.setattr(geocoding, "_next_request", 0)

    def offline(*_):
        raise OSError("offline")

    monkeypatch.setattr(geocoding, "request_address", offline)
    assert client.get("/api/v1/map/reverse?latitude=55&longitude=37").status_code == 503
    response = client.post(
        "/api/v1/projects",
        json=dict(
            name="Координаты", project_type_id="roads", latitude=55, longitude=37
        ),
        headers={"Idempotency-Key": "offline"},
    )
    assert response.status_code == 201
    assert response.json()["latitude"] == 55


def test_malformed_provider_response_is_unavailable(client, monkeypatch):
    geocoding._cache.clear()
    monkeypatch.setattr(geocoding, "_next_request", 0)
    monkeypatch.setattr(
        geocoding, "urlopen", lambda *a, **kw: BytesIO(b'{"features":[null]}')
    )
    assert client.get("/api/v1/map/reverse?latitude=55&longitude=37").status_code == 503


def test_forward_search_finds_and_caches_location(client, monkeypatch):
    geocoding._cache.clear()
    monkeypatch.setattr(geocoding, "_next_request", 0)
    calls = []

    def lookup(address):
        calls.append(address)
        return [
            {
                "latitude": 55.751244,
                "longitude": 37.618423,
                "address": "Москва, Красная площадь",
            }
        ]

    monkeypatch.setattr(geocoding, "request_locations", lookup)
    url = "/api/v1/map/search?address=Москва%2C%20Красная%20площадь"
    first = client.get(url)
    second = client.get(url)
    assert first.status_code == 200
    assert first.json()["latitude"] == 55.751244
    assert first.json()["longitude"] == 37.618423
    assert first.json()["suggestions"][0]["address"] == "Москва, Красная площадь"
    assert second.json() == first.json()
    assert calls == ["Москва, Красная площадь"]


def test_forward_search_accepts_no_results(client, monkeypatch):
    geocoding._cache.clear()
    monkeypatch.setattr(geocoding, "_next_request", 0)
    monkeypatch.setattr(geocoding, "request_locations", lambda _: [])
    response = client.get("/api/v1/map/search?address=Несуществующий%20адрес")
    assert response.status_code == 200
    assert response.json()["latitude"] is None
    assert response.json()["longitude"] is None
    assert response.json()["suggestions"] == []


def test_forward_provider_response_maps_coordinates(monkeypatch):
    payload = """{
      "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [37.618423, 55.751244]},
        "properties": {"city": "Москва", "name": "Красная площадь"}
      }]
    }""".encode()
    monkeypatch.setattr(geocoding, "urlopen", lambda *a, **kw: BytesIO(payload))
    assert geocoding.request_location("Москва, Красная площадь") == {
        "latitude": 55.751244,
        "longitude": 37.618423,
        "address": "Москва, Красная площадь",
    }
