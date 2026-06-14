import math

import requests
from django.conf import settings


class OSMRoutingService:
    def __init__(self):
        self.base_url = str(getattr(settings, "OSM_ROUTING_BASE_URL", "") or "").rstrip("/")
        self.timeout_seconds = int(getattr(settings, "OSM_ROUTING_TIMEOUT_SECONDS", 8) or 8)

    def build_walking_route(self, start: dict, end: dict):
        if not self._is_valid_point(start) or not self._is_valid_point(end):
            return self._fallback_route(start, end, reason="missing_coordinates")

        if self.base_url:
            try:
                return self._request_osrm_route(start, end)
            except requests.RequestException:
                return self._fallback_route(start, end, reason="request_exception")
            except (ValueError, KeyError, TypeError):
                return self._fallback_route(start, end, reason="invalid_response")

        return self._fallback_route(start, end, reason="routing_base_url_not_configured")

    def _request_osrm_route(self, start: dict, end: dict):
        url = (
            f"{self.base_url}/route/v1/foot/"
            f"{start['lng']},{start['lat']};{end['lng']},{end['lat']}"
            "?overview=full&geometries=geojson&steps=true"
        )
        response = requests.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        routes = payload.get("routes") or []
        if not routes:
            raise ValueError("No routes returned by OSRM provider.")
        route = routes[0]
        coordinates = (route.get("geometry") or {}).get("coordinates") or []
        polyline = [
            {"lat": float(lat), "lng": float(lng)}
            for lng, lat in coordinates
            if lat is not None and lng is not None
        ]
        if len(polyline) < 2:
            raise ValueError("Route geometry is incomplete.")
        steps = self._serialize_osrm_steps(route.get("legs") or [])
        return {
            "provider": "OSM_OSRM",
            "distance_meters": float(route.get("distance") or 0),
            "duration_seconds": int(route.get("duration") or 0),
            "polyline": polyline,
            "metadata": {
                "code": payload.get("code", ""),
                "steps": steps,
            },
        }

    def _fallback_route(self, start: dict, end: dict, reason: str):
        if not self._is_valid_point(start) or not self._is_valid_point(end):
            polyline = []
            distance_meters = 0.0
            duration_seconds = 0
            steps = []
        else:
            polyline = [
                {"lat": float(start["lat"]), "lng": float(start["lng"])},
                {"lat": float(end["lat"]), "lng": float(end["lng"])},
            ]
            distance_meters = self._haversine_distance_meters(start["lat"], start["lng"], end["lat"], end["lng"])
            duration_seconds = int(distance_meters / 1.35) if distance_meters > 0 else 0
            steps = [
                {
                    "index": 1,
                    "instruction": "Dirigete al destino siguiendo la ruta sugerida.",
                    "distance_meters": float(distance_meters),
                    "duration_seconds": int(duration_seconds),
                    "street_name": "",
                    "maneuver_type": "direct",
                    "maneuver_modifier": "",
                    "start": {"lat": float(start["lat"]), "lng": float(start["lng"])},
                    "end": {"lat": float(end["lat"]), "lng": float(end["lng"])},
                }
            ]
        return {
            "provider": "OSM_FALLBACK",
            "distance_meters": float(distance_meters),
            "duration_seconds": duration_seconds,
            "polyline": polyline,
            "metadata": {
                "fallback_reason": reason,
                "steps": steps,
            },
        }

    def _serialize_osrm_steps(self, legs: list):
        serialized = []
        step_index = 1
        for leg in legs:
            for step in leg.get("steps") or []:
                maneuver = step.get("maneuver") or {}
                start_lat, start_lng = self._coord_pair(maneuver.get("location"))
                end_lat, end_lng = self._step_end_point(step)
                serialized.append(
                    {
                        "index": step_index,
                        "instruction": self._build_instruction(
                            maneuver_type=str(maneuver.get("type") or ""),
                            modifier=str(maneuver.get("modifier") or ""),
                            street_name=str(step.get("name") or ""),
                        ),
                        "distance_meters": float(step.get("distance") or 0),
                        "duration_seconds": int(step.get("duration") or 0),
                        "street_name": str(step.get("name") or ""),
                        "maneuver_type": str(maneuver.get("type") or ""),
                        "maneuver_modifier": str(maneuver.get("modifier") or ""),
                        "start": {"lat": start_lat, "lng": start_lng},
                        "end": {"lat": end_lat, "lng": end_lng},
                    }
                )
                step_index += 1
        return serialized

    def _step_end_point(self, step: dict):
        geometry = step.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if coordinates:
            lng, lat = coordinates[-1]
            return self._as_float(lat), self._as_float(lng)
        return self._coord_pair((step.get("maneuver") or {}).get("location"))

    def _coord_pair(self, value):
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            return None, None
        lng, lat = value[0], value[1]
        return self._as_float(lat), self._as_float(lng)

    def _as_float(self, value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _build_instruction(self, maneuver_type: str, modifier: str, street_name: str):
        modifier_label = self._modifier_label(modifier)
        if maneuver_type == "depart":
            base = "Inicia el recorrido"
        elif maneuver_type == "arrive":
            base = "Llegaste al destino"
        elif maneuver_type == "turn":
            base = f"Gira {modifier_label}".strip()
        elif maneuver_type == "continue":
            base = f"Continua {modifier_label}".strip()
        elif maneuver_type == "new name":
            base = "Continua por la via principal"
        elif maneuver_type == "roundabout":
            base = "Ingresa a la rotonda"
        elif maneuver_type == "exit roundabout":
            base = "Sal de la rotonda"
        elif maneuver_type == "fork":
            base = f"Toma la bifurcacion {modifier_label}".strip()
        elif maneuver_type == "end of road":
            base = f"Al final de la via gira {modifier_label}".strip()
        else:
            base = "Sigue la ruta sugerida"
        if street_name:
            if maneuver_type == "arrive":
                return f"{base} en {street_name}."
            return f"{base} por {street_name}."
        return f"{base}."

    def _modifier_label(self, modifier: str):
        labels = {
            "left": "a la izquierda",
            "right": "a la derecha",
            "straight": "recto",
            "slight left": "ligeramente a la izquierda",
            "slight right": "ligeramente a la derecha",
            "sharp left": "cerrado a la izquierda",
            "sharp right": "cerrado a la derecha",
            "uturn": "en U",
        }
        return labels.get(modifier, "").strip()

    def _haversine_distance_meters(self, lat1: float, lng1: float, lat2: float, lng2: float):
        radius = 6371000
        phi_1 = math.radians(lat1)
        phi_2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lng2 - lng1)
        a = (
            math.sin(delta_phi / 2) ** 2
            + math.cos(phi_1) * math.cos(phi_2) * math.sin(delta_lambda / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return radius * c

    def _is_valid_point(self, point: dict | None):
        if not point:
            return False
        return point.get("lat") is not None and point.get("lng") is not None
