from uuid import UUID

from django.db import transaction
from django.utils import timezone

from modules.delivery_logistica.models import (
    DeliveryAssignment,
    DeliveryLocationPing,
    DeliveryRouteSnapshot,
)
from modules.gestion_cocinero.models import ChefProfile
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.pedidos_checkout_pagos.realtime import publish_order_tracking_refresh

from .osm_routing_service import OSMRoutingService


class DeliveryTrackingError(ValueError):
    def __init__(self, message: str, code: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class DeliveryTrackingService:
    PRE_PICKUP_STATUSES = {
        DeliveryAssignment.Status.UNASSIGNED,
        DeliveryAssignment.Status.ASSIGNED,
        DeliveryAssignment.Status.AT_CHEF,
    }

    def __init__(self):
        self.routing_service = OSMRoutingService()

    @transaction.atomic
    def record_delivery_location(self, user_id: str, assignment_id: str, payload: dict):
        delivery = self._require_delivery(user_id)
        assignment = self._get_owned_assignment(delivery, assignment_id, lock=True)
        if assignment.status in {DeliveryAssignment.Status.DELIVERED, DeliveryAssignment.Status.CANCELLED, DeliveryAssignment.Status.FAILED}:
            raise DeliveryTrackingError(
                "La entrega ya no admite reportes de ubicacion.",
                "assignment_tracking_closed",
            )
        ping = DeliveryLocationPing.objects.create(
            assignment=assignment,
            source=DeliveryLocationPing.Source.DELIVERY_APP,
            latitude=float(payload["latitude"]),
            longitude=float(payload["longitude"]),
            accuracy_meters=payload.get("accuracy_meters"),
            speed_mps=payload.get("speed_mps"),
            heading_degrees=payload.get("heading_degrees"),
            recorded_at=payload.get("recorded_at") or timezone.now(),
        )
        map_payload = self.refresh_current_route(assignment)
        if assignment.delivery_user_id:
            from modules.delivery_logistica.realtime import publish_assignment_snapshot_for_delivery

            publish_assignment_snapshot_for_delivery(
                assignment.id,
                str(assignment.delivery_user.supabase_user_id),
            )
        publish_order_tracking_refresh(str(assignment.order_id))
        return {
            "assignment_id": assignment.id,
            "location_ping": self._serialize_ping(ping),
            "map": map_payload,
        }

    def get_current_location(self, user_id: str, assignment_id: str):
        delivery = self._require_delivery(user_id)
        assignment = self._get_assignment_for_delivery(delivery, assignment_id)
        ping = self._latest_ping(assignment)
        return {
            "assignment_id": assignment.id,
            "current_location": self._serialize_ping(ping),
        }

    def get_route_snapshot(self, user_id: str, assignment_id: str):
        delivery = self._require_delivery(user_id)
        assignment = self._get_assignment_for_delivery(delivery, assignment_id)
        map_payload = self.ensure_map_payload(assignment)
        return {
            "assignment_id": assignment.id,
            "route": map_payload.get("route"),
            "navigation": map_payload.get("navigation"),
            "map": map_payload,
        }

    @transaction.atomic
    def refresh_route_for_delivery(self, user_id: str, assignment_id: str):
        delivery = self._require_delivery(user_id)
        assignment = self._get_owned_assignment(delivery, assignment_id, lock=True)
        map_payload = self.refresh_current_route(assignment)
        if assignment.delivery_user_id:
            from modules.delivery_logistica.realtime import publish_assignment_snapshot_for_delivery

            publish_assignment_snapshot_for_delivery(
                assignment.id,
                str(assignment.delivery_user.supabase_user_id),
            )
        publish_order_tracking_refresh(str(assignment.order_id))
        return {
            "assignment_id": assignment.id,
            "route": map_payload.get("route"),
            "navigation": map_payload.get("navigation"),
            "map": map_payload,
        }

    def ensure_map_payload(self, assignment: DeliveryAssignment):
        latest_ping = self._latest_ping(assignment)
        current_route = assignment.route_snapshots.filter(is_current=True).order_by("-updated_at").first()
        if not current_route:
            self.refresh_current_route(assignment, latest_ping=latest_ping)
            current_route = assignment.route_snapshots.filter(is_current=True).order_by("-updated_at").first()
        return self._serialize_map(assignment, latest_ping=latest_ping, route=current_route)

    @transaction.atomic
    def refresh_current_route(self, assignment: DeliveryAssignment, latest_ping: DeliveryLocationPing | None = None):
        latest_ping = latest_ping or self._latest_ping(assignment)
        route_kind = (
            DeliveryRouteSnapshot.RouteKind.TO_CHEF
            if assignment.status in self.PRE_PICKUP_STATUSES
            else DeliveryRouteSnapshot.RouteKind.TO_CLIENT
        )
        start = self._route_start_point(assignment, latest_ping)
        end = self._route_end_point(assignment, route_kind)

        assignment.route_snapshots.filter(is_current=True).update(is_current=False, updated_at=timezone.now())
        if start["lat"] is not None and start["lng"] is not None and end["lat"] is not None and end["lng"] is not None:
            route_result = self.routing_service.build_walking_route(start, end)
            DeliveryRouteSnapshot.objects.create(
                assignment=assignment,
                route_kind=route_kind,
                provider=route_result["provider"],
                is_current=True,
                start_latitude=float(start["lat"]),
                start_longitude=float(start["lng"]),
                end_latitude=float(end["lat"]),
                end_longitude=float(end["lng"]),
                distance_meters=float(route_result["distance_meters"]),
                duration_seconds=int(route_result["duration_seconds"]),
                polyline=route_result["polyline"],
                metadata={
                    **(route_result.get("metadata") or {}),
                    "origin_kind": start.get("kind", ""),
                    "origin_label": start.get("label", ""),
                    "destination_kind": end.get("kind", ""),
                    "destination_label": end.get("label", ""),
                    "origin_used": {
                        "kind": start.get("kind", ""),
                        "lat": float(start["lat"]) if start["lat"] is not None else None,
                        "lng": float(start["lng"]) if start["lng"] is not None else None,
                        "label": start.get("label", ""),
                        "address": start.get("address", ""),
                    },
                    "destination_used": {
                        "kind": end.get("kind", ""),
                        "lat": float(end["lat"]) if end["lat"] is not None else None,
                        "lng": float(end["lng"]) if end["lng"] is not None else None,
                        "label": end.get("label", ""),
                        "address": end.get("address", ""),
                    },
                },
            )
        return self._serialize_map(
            assignment,
            latest_ping=latest_ping,
            route=assignment.route_snapshots.filter(is_current=True).order_by("-updated_at").first(),
        )

    def _serialize_map(
        self,
        assignment: DeliveryAssignment,
        latest_ping: DeliveryLocationPing | None,
        route: DeliveryRouteSnapshot | None,
    ):
        chef_point = self._chef_point(assignment)
        client_point = self._client_point(assignment)
        origin = self._route_origin_point(assignment, latest_ping)
        destination = self._route_destination_point(assignment, route)
        current_location = self._serialize_ping(latest_ping)
        grouped_route = self._build_grouped_route(assignment, latest_ping=latest_ping)
        quality = self._route_quality_payload(route, origin, destination)
        return {
            "enabled": bool(route or latest_ping or chef_point["lat"] is not None or client_point["lat"] is not None),
            "provider": route.provider if route else "",
            "tile_url": "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "route_kind": route.route_kind if route else "",
            "current_location": current_location,
            "markers": {
                "chef": {
                    "lat": chef_point["lat"],
                    "lng": chef_point["lng"],
                    "label": self._profile_name(assignment.order.chef),
                    "address": chef_point["address"],
                },
                "client": {
                    "lat": client_point["lat"],
                    "lng": client_point["lng"],
                    "label": self._profile_name(assignment.order.client),
                    "address": client_point["address"],
                },
                "delivery_current": current_location,
            },
            "route": self._serialize_route(route),
            "navigation": self._serialize_navigation(route, origin, destination),
            "grouped_route": grouped_route,
            "quality": quality,
            "last_ping_at": current_location["recorded_at"] if current_location else None,
        }

    def _serialize_route(self, route: DeliveryRouteSnapshot | None):
        if not route:
            return None
        return {
            "id": route.id,
            "route_kind": route.route_kind,
            "provider": route.provider,
            "distance_meters": float(route.distance_meters),
            "duration_seconds": int(route.duration_seconds),
            "polyline": route.polyline or [],
            "updated_at": route.updated_at.isoformat(),
            "start": {
                "lat": route.start_latitude,
                "lng": route.start_longitude,
            },
            "end": {
                "lat": route.end_latitude,
                "lng": route.end_longitude,
            },
            "quality": self._route_quality_payload(route, None, None),
        }

    def _serialize_ping(self, ping: DeliveryLocationPing | None):
        if not ping:
            return None
        return {
            "lat": float(ping.latitude),
            "lng": float(ping.longitude),
            "accuracy_meters": float(ping.accuracy_meters) if ping.accuracy_meters is not None else None,
            "speed_mps": float(ping.speed_mps) if ping.speed_mps is not None else None,
            "heading_degrees": float(ping.heading_degrees) if ping.heading_degrees is not None else None,
            "recorded_at": ping.recorded_at.isoformat(),
            "source": ping.source,
        }

    def _route_start_point(self, assignment: DeliveryAssignment, latest_ping: DeliveryLocationPing | None):
        if latest_ping:
            return {
                "kind": "CURRENT_LOCATION",
                "lat": latest_ping.latitude,
                "lng": latest_ping.longitude,
                "address": "",
                "label": "Tu ubicacion actual",
            }
        delivery_known_point = self._delivery_known_point(assignment)
        if delivery_known_point["lat"] is not None and delivery_known_point["lng"] is not None:
            return delivery_known_point
        if assignment.status in self.PRE_PICKUP_STATUSES:
            return {"lat": None, "lng": None, "address": "", "kind": "UNAVAILABLE", "label": ""}
        return self._chef_point(assignment)

    def _route_end_point(self, assignment: DeliveryAssignment, route_kind: str):
        if route_kind == DeliveryRouteSnapshot.RouteKind.TO_CHEF:
            return self._chef_point(assignment)
        return self._client_point(assignment)

    def _route_origin_point(self, assignment: DeliveryAssignment, latest_ping: DeliveryLocationPing | None):
        if latest_ping:
            return {
                "kind": "CURRENT_LOCATION",
                "lat": float(latest_ping.latitude),
                "lng": float(latest_ping.longitude),
                "label": "Tu ubicacion actual",
                "address": "",
            }
        delivery_known_point = self._delivery_known_point(assignment)
        if delivery_known_point["lat"] is not None and delivery_known_point["lng"] is not None:
            return delivery_known_point
        if assignment.status in self.PRE_PICKUP_STATUSES:
            return None
        chef_point = self._chef_point(assignment)
        if chef_point["lat"] is None or chef_point["lng"] is None:
            return None
        return {
            "kind": "CHEF",
            "lat": float(chef_point["lat"]),
            "lng": float(chef_point["lng"]),
            "label": self._profile_name(assignment.order.chef),
            "address": chef_point["address"],
        }

    def _route_destination_point(self, assignment: DeliveryAssignment, route: DeliveryRouteSnapshot | None):
        route_kind = route.route_kind if route else (
            DeliveryRouteSnapshot.RouteKind.TO_CHEF
            if assignment.status in self.PRE_PICKUP_STATUSES
            else DeliveryRouteSnapshot.RouteKind.TO_CLIENT
        )
        if route_kind == DeliveryRouteSnapshot.RouteKind.TO_CHEF:
            chef_point = self._chef_point(assignment)
            if chef_point["lat"] is None or chef_point["lng"] is None:
                return None
            return {
                "kind": "CHEF",
                "lat": float(chef_point["lat"]),
                "lng": float(chef_point["lng"]),
                "label": self._profile_name(assignment.order.chef),
                "address": chef_point["address"],
            }
        client_point = self._client_point(assignment)
        if client_point["lat"] is None or client_point["lng"] is None:
            return None
        return {
            "kind": "CLIENT",
            "lat": float(client_point["lat"]),
            "lng": float(client_point["lng"]),
            "label": self._profile_name(assignment.order.client),
            "address": client_point["address"],
        }

    def _serialize_navigation(
        self,
        route: DeliveryRouteSnapshot | None,
        origin: dict | None,
        destination: dict | None,
    ):
        if not route:
            return None
        steps = (route.metadata or {}).get("steps") or []
        next_step = None
        for step in steps:
            if step.get("maneuver_type") != "arrive":
                next_step = step
                break
        if next_step is None and steps:
            next_step = steps[0]
        return {
            "mode": "walking",
            "route_kind": route.route_kind,
            "origin": origin,
            "destination": destination,
            "summary": {
                "distance_meters": float(route.distance_meters),
                "duration_seconds": int(route.duration_seconds),
                "distance_human": self._distance_human(route.distance_meters),
                "duration_human": self._duration_human(route.duration_seconds),
                "updated_at": route.updated_at.isoformat(),
                "provider": route.provider,
                "uses_fallback": bool((route.metadata or {}).get("uses_fallback")),
                "fallback_reason": str((route.metadata or {}).get("fallback_reason") or ""),
            },
            "next_step": next_step,
            "steps": steps[:8],
        }

    def _route_quality_payload(self, route: DeliveryRouteSnapshot | None, origin: dict | None, destination: dict | None):
        metadata = route.metadata if route else {}
        return {
            "provider": route.provider if route else "",
            "uses_fallback": bool((metadata or {}).get("uses_fallback")),
            "fallback_reason": str((metadata or {}).get("fallback_reason") or ""),
            "origin_used": (
                (metadata or {}).get("origin_used")
                or self._waypoint_payload(origin)
            ),
            "destination_used": (
                (metadata or {}).get("destination_used")
                or self._waypoint_payload(destination)
            ),
        }

    def _build_grouped_route(self, assignment: DeliveryAssignment, latest_ping: DeliveryLocationPing | None = None):
        if not assignment.delivery_user_id:
            return None
        active_assignments = list(
            DeliveryAssignment.objects.filter(
                delivery_user=assignment.delivery_user,
                status__in=[
                    DeliveryAssignment.Status.ASSIGNED,
                    DeliveryAssignment.Status.AT_CHEF,
                    DeliveryAssignment.Status.PICKED_UP,
                    DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
                ],
            )
            .select_related("order", "order__client", "order__chef")
            .prefetch_related("order__address")
            .order_by("assigned_at", "created_at")
        )
        if not active_assignments:
            return None
        start_point = self._route_start_point(assignment, latest_ping)
        if start_point["lat"] is None or start_point["lng"] is None:
            start_point = self._delivery_known_point(assignment)
        current_lat = start_point["lat"]
        current_lng = start_point["lng"]
        pickup_stops = []
        delivery_stops = []
        for item in active_assignments:
            if item.status in {
                DeliveryAssignment.Status.ASSIGNED,
                DeliveryAssignment.Status.AT_CHEF,
            }:
                stop = self._chef_stop_payload(item)
                if self._is_valid_waypoint(stop):
                    pickup_stops.append(stop)
            if item.status in {
                DeliveryAssignment.Status.PICKED_UP,
                DeliveryAssignment.Status.EN_ROUTE_TO_CLIENT,
            }:
                stop = self._client_stop_payload(item)
                if self._is_valid_waypoint(stop):
                    delivery_stops.append(stop)
        ordered_pickups = self._order_stops_by_distance(pickup_stops, current_lat, current_lng)
        if ordered_pickups:
            current_lat = ordered_pickups[-1]["lat"]
            current_lng = ordered_pickups[-1]["lng"]
        ordered_deliveries = self._order_stops_by_distance(delivery_stops, current_lat, current_lng)
        ordered_stops = [*ordered_pickups, *ordered_deliveries]
        total_distance = 0.0
        total_seconds = 0
        segment_origin = start_point
        polyline = []
        for stop in ordered_stops:
            if not self._is_valid_waypoint(segment_origin) or not self._is_valid_waypoint(stop):
                segment_origin = stop
                continue
            segment = self.routing_service.build_walking_route(segment_origin, stop)
            total_distance += float(segment["distance_meters"] or 0)
            total_seconds += int(segment["duration_seconds"] or 0)
            segment_points = segment.get("polyline") or []
            if polyline and segment_points:
                polyline.extend(segment_points[1:])
            else:
                polyline.extend(segment_points)
            segment_origin = stop
        return {
            "delivery_user_id": str(assignment.delivery_user.supabase_user_id),
            "delivery_user_name": self._profile_name(assignment.delivery_user),
            "active_assignment_count": len(active_assignments),
            "distance_meters": total_distance,
            "duration_seconds": total_seconds,
            "distance_human": self._distance_human(total_distance),
            "duration_human": self._duration_human(total_seconds),
            "polyline": polyline,
            "stops": ordered_stops,
            "next_stop": ordered_stops[0] if ordered_stops else None,
        }

    def _order_stops_by_distance(self, stops: list[dict], current_lat, current_lng):
        if current_lat is None or current_lng is None:
            return sorted(stops, key=lambda item: (item.get("priority", 99), item.get("eta_seed_seconds", 0)))
        pending = list(stops)
        ordered = []
        origin_lat = float(current_lat)
        origin_lng = float(current_lng)
        while pending:
            pending.sort(
                key=lambda item: (
                    int(item.get("priority", 99)),
                    self.routing_service._haversine_distance_meters(origin_lat, origin_lng, item["lat"], item["lng"]),
                )
            )
            next_stop = pending.pop(0)
            ordered.append(next_stop)
            origin_lat = float(next_stop["lat"])
            origin_lng = float(next_stop["lng"])
        return ordered

    def _chef_stop_payload(self, assignment: DeliveryAssignment):
        point = self._chef_point(assignment)
        return {
            "kind": "CHEF_PICKUP",
            "assignment_id": assignment.id,
            "order_id": assignment.order_id,
            "status": assignment.status,
            "status_label": "Recoger del cocinero",
            "lat": point["lat"],
            "lng": point["lng"],
            "label": self._profile_name(assignment.order.chef),
            "address": point["address"],
            "priority": 1,
            "eta_seed_seconds": 0,
        }

    def _client_stop_payload(self, assignment: DeliveryAssignment):
        point = self._client_point(assignment)
        return {
            "kind": "CLIENT_DELIVERY",
            "assignment_id": assignment.id,
            "order_id": assignment.order_id,
            "status": assignment.status,
            "status_label": "Entregar al cliente",
            "lat": point["lat"],
            "lng": point["lng"],
            "label": self._profile_name(assignment.order.client),
            "address": point["address"],
            "priority": 2,
            "eta_seed_seconds": 0,
        }

    def _is_valid_waypoint(self, point: dict | None):
        return bool(point and point.get("lat") is not None and point.get("lng") is not None)

    def _waypoint_payload(self, point: dict | None):
        if not point:
            return None
        return {
            "kind": point.get("kind", ""),
            "lat": float(point["lat"]) if point.get("lat") is not None else None,
            "lng": float(point["lng"]) if point.get("lng") is not None else None,
            "label": point.get("label", ""),
            "address": point.get("address", ""),
        }

    def _delivery_known_point(self, assignment: DeliveryAssignment):
        latest_known_ping = None
        if assignment.delivery_user_id:
            latest_known_ping = (
                DeliveryLocationPing.objects.filter(assignment__delivery_user=assignment.delivery_user)
                .order_by("-recorded_at", "-created_at")
                .first()
            )
        if latest_known_ping:
            return {
                "kind": "DELIVERY_LAST_KNOWN",
                "lat": float(latest_known_ping.latitude),
                "lng": float(latest_known_ping.longitude),
                "label": "Ultima ubicacion conocida",
                "address": "",
            }
        if assignment.delivery_user and assignment.delivery_user.location_latitude is not None and assignment.delivery_user.location_longitude is not None:
            return {
                "kind": "DELIVERY_PROFILE",
                "lat": float(assignment.delivery_user.location_latitude),
                "lng": float(assignment.delivery_user.location_longitude),
                "label": "Ubicacion base del repartidor",
                "address": assignment.delivery_user.address or "",
            }
        return {"lat": None, "lng": None, "address": "", "kind": "", "label": ""}

    def _distance_human(self, value: float):
        distance = float(value or 0)
        if distance >= 1000:
            return f"{distance / 1000:.1f} km"
        return f"{int(round(distance))} m"

    def _duration_human(self, value: int):
        seconds = int(value or 0)
        if seconds >= 3600:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            if minutes:
                return f"{hours} h {minutes} min"
            return f"{hours} h"
        minutes = max(1, round(seconds / 60)) if seconds else 0
        return f"{minutes} min" if minutes else "0 min"

    def _chef_point(self, assignment: DeliveryAssignment):
        profile = ChefProfile.objects.filter(user=assignment.order.chef).first()
        return {
            "kind": "CHEF",
            "lat": profile.location_latitude if profile else None,
            "lng": profile.location_longitude if profile else None,
            "address": profile.location_address if profile else "",
            "label": self._profile_name(assignment.order.chef),
        }

    def _client_point(self, assignment: DeliveryAssignment):
        address = getattr(assignment.order, "address", None)
        return {
            "kind": "CLIENT",
            "lat": address.latitude if address else None,
            "lng": address.longitude if address else None,
            "address": address.line_1 if address else "",
            "label": self._profile_name(assignment.order.client),
        }

    def _latest_ping(self, assignment: DeliveryAssignment):
        return assignment.location_pings.order_by("-recorded_at", "-created_at").first()

    def _require_delivery(self, user_id: str):
        parsed = self._parse_uuid(user_id)
        profile = UserProfile.objects.filter(supabase_user_id=parsed, role=UserProfile.ROLE_DELIVERY).first() if parsed else None
        if not profile:
            raise DeliveryTrackingError("Perfil de repartidor no encontrado.", "delivery_not_found")
        return profile

    def _get_assignment_for_delivery(self, delivery: UserProfile, assignment_id: str):
        assignment = (
            DeliveryAssignment.objects.filter(id=str(assignment_id), delivery_user=delivery)
            .select_related("order", "order__client", "order__chef")
            .prefetch_related("order__address", "location_pings", "route_snapshots")
            .first()
        )
        if not assignment:
            foreign_assignment = DeliveryAssignment.objects.select_related("delivery_user").filter(id=str(assignment_id)).first()
            if foreign_assignment and foreign_assignment.delivery_user_id and foreign_assignment.delivery_user_id != delivery.id:
                raise DeliveryTrackingError(
                    "La entrega ya no pertenece a tu usuario. Fue reasignada a otro repartidor.",
                    "assignment_reassigned",
                )
            raise DeliveryTrackingError("Entrega no encontrada para el repartidor.", "assignment_not_found")
        return assignment

    def _get_owned_assignment(self, delivery: UserProfile, assignment_id: str, lock: bool = False):
        queryset = DeliveryAssignment.objects.filter(id=str(assignment_id), delivery_user=delivery)
        if lock:
            queryset = queryset.select_for_update()
        assignment = (
            queryset.select_related("order", "order__client", "order__chef")
            .prefetch_related("order__address", "location_pings", "route_snapshots")
            .first()
        )
        if not assignment:
            foreign_assignment = DeliveryAssignment.objects.select_related("delivery_user").filter(id=str(assignment_id)).first()
            if foreign_assignment and foreign_assignment.delivery_user_id and foreign_assignment.delivery_user_id != delivery.id:
                raise DeliveryTrackingError(
                    "La entrega ya no pertenece a tu usuario. Fue reasignada a otro repartidor.",
                    "assignment_reassigned",
                )
            raise DeliveryTrackingError("Entrega no encontrada para el repartidor.", "assignment_not_found")
        return assignment

    def _parse_uuid(self, value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None

    def _profile_name(self, profile: UserProfile | None):
        if not profile:
            return ""
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or profile.full_name or profile.email
