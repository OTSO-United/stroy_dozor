import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "./api";

const layers = {
  streets: {
    name: "Улицы",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>',
  },
  satellite: {
    name: "Спутник",
    url: "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution:
      "Tiles &copy; Esri - Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
  },
};

export type Location = { latitude: number; longitude: number } | null;
export function LocationPicker({
  value,
  onChange,
  onAddress,
}: {
  value: Location;
  onChange: (value: Location) => void;
  onAddress: (address: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null),
    map = useRef<L.Map | null>(null),
    marker = useRef<L.CircleMarker | null>(null);
  const [layer, setLayer] = useState<keyof typeof layers>("streets");
  const [lookupPoint, setLookupPoint] = useState<Location>(null);
  const [lookupVersion, setLookupVersion] = useState(0);
  const [lookupStatus, setLookupStatus] = useState("");
  const callbacks = useRef({ onChange, onAddress });
  callbacks.current = { onChange, onAddress };
  const selectPoint = (point: Location) => {
    callbacks.current.onChange(point);
    setLookupPoint(point);
    setLookupVersion((v) => v + 1);
    setLookupStatus(point ? "Определяем адрес…" : "");
  };
  const onSelect = useRef(selectPoint);
  onSelect.current = selectPoint;
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!container.current) return;
    const view = L.map(container.current, { scrollWheelZoom: false }).setView(
      value ? [value.latitude, value.longitude] : [55.751244, 37.618423],
      value ? 15 : 10,
    );
    map.current = view;
    view.attributionControl.setPrefix(
      '<a href="https://leafletjs.com" target="_blank" rel="noreferrer">Leaflet</a>',
    );
    view.on("click", (event: L.LeafletMouseEvent) => {
      const point = event.latlng.wrap();
      onSelect.current({
        latitude: +point.lat.toFixed(6),
        longitude: +point.lng.toFixed(6),
      });
    });
    const observer = new ResizeObserver(() => view.invalidateSize());
    observer.observe(container.current);
    return () => {
      observer.disconnect();
      view.remove();
      map.current = null;
      marker.current = null;
    };
  }, []);
  useEffect(() => {
    const view = map.current;
    if (!view) return;
    setFailed(false);
    const selected = layers[layer];
    const tiles = L.tileLayer(selected.url, {
      maxZoom: 19,
      attribution: selected.attribution,
    })
      .on("tileerror", () => setFailed(true))
      .addTo(view);
    return () => {
      tiles.remove();
    };
  }, [layer]);
  useEffect(() => {
    if (!lookupPoint) return;
    const controller = new AbortController();
    // Debounce rapid point changes; stale responses never replace a newer address.
    const timer = setTimeout(() => {
      api<{ address: string | null }>(
        `/map/reverse?latitude=${lookupPoint.latitude}&longitude=${lookupPoint.longitude}`,
        { signal: controller.signal },
      )
        .then((result) => {
          if (controller.signal.aborted) return;
          if (result.address) {
            callbacks.current.onAddress(result.address);
            setLookupStatus(
              "Адрес ближайшего объекта определён. При необходимости уточните его в поле выше.",
            );
          } else
            setLookupStatus(
              "Адрес рядом с точкой не найден. Введите его вручную - точка всё равно сохранится.",
            );
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setLookupStatus(`${error.message}. Адрес можно ввести вручную.`);
        });
    }, 650);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [lookupPoint, lookupVersion]);
  useEffect(() => {
    if (!map.current) return;
    if (marker.current) {
      marker.current.remove();
      marker.current = null;
    }
    if (value) {
      marker.current = L.circleMarker([value.latitude, value.longitude], {
        radius: 9,
        color: "#174f40",
        fillColor: "#29a980",
        fillOpacity: 1,
        weight: 3,
      }).addTo(map.current);
      map.current.setView(
        [value.latitude, value.longitude],
        Math.max(map.current.getZoom(), 15),
      );
    }
  }, [value]);
  return (
    <div className="location-picker">
      <label className="map-layer-select">
        Вид карты
        <select
          aria-label="Вид карты"
          value={layer}
          onChange={(e) => setLayer(e.target.value as keyof typeof layers)}
        >
          {Object.entries(layers).map(([key, item]) => (
            <option key={key} value={key}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <div
        ref={container}
        className="location-map"
        role="region"
        aria-label="Выбор точки объекта на карте"
      />
      <div className="map-actions">
        <span>
          {value ? "Точка выбрана" : "Нажмите на карте, чтобы отметить объект"}
        </span>
        <button
          type="button"
          className="text-button"
          onClick={() => {
            const point = map.current?.getCenter().wrap();
            if (point)
              selectPoint({
                latitude: +point.lat.toFixed(6),
                longitude: +point.lng.toFixed(6),
              });
          }}
        >
          Выбрать центр карты
        </button>
        {value && (
          <button
            type="button"
            className="text-button destructive"
            onClick={() => selectPoint(null)}
          >
            Убрать точку
          </button>
        )}
      </div>
      {lookupStatus && (
        <p className="hint" role="status">
          {lookupStatus}
        </p>
      )}
      {value && (
        <button
          type="button"
          className="text-button"
          onClick={() => selectPoint(value)}
        >
          Определить адрес повторно
        </button>
      )}
      <p className="hint map-credit">
        Адрес:{" "}
        <a href="https://photon.komoot.io" target="_blank" rel="noreferrer">
          Photon
        </a>{" "}
        / © OpenStreetMap. Точка и адрес сохраняются кнопкой формы.
      </p>
      {failed && (
        <p role="status" className="inline-warning">
          Карта недоступна. Проверьте интернет; адрес и ранее выбранная точка
          сохраняются.
        </p>
      )}
    </div>
  );
}
