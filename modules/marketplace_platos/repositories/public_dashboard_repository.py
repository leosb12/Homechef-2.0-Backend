from math import asin, cos, radians, sin, sqrt

from django.db.models import Q

from modules.gestion_cocinero.models import ChefAvailability, ChefProfile, DailyMenu, Dish
from modules.gestion_cocinero.services.availability_rules import is_open_now
from modules.marketplace_platos.models import MarketplaceReview
from modules.pedidos_checkout_pagos.services import build_dish_stock_snapshot


class PublicDashboardRepository:
    """PostgreSQL repository for CU-07/CU-08 based on active chef menus."""

    def fetch_public_dishes(self, latitude: str = "", longitude: str = ""):
        docs = self._build_marketplace_docs(latitude=latitude, longitude=longitude)
        docs.sort(key=lambda d: (0 if d["is_featured"] else 1, d["name"].lower()))
        return docs

    def fetch_client_explore(
        self,
        query: str = "",
        featured: str = "",
        sort: str = "",
        min_price: str = "",
        max_price: str = "",
        max_distance_km: str = "",
        availability: str = "",
        cuisine_type: str = "",
        diet_type: str = "",
        latitude: str = "",
        longitude: str = "",
    ):
        docs = self._build_marketplace_docs(latitude=latitude, longitude=longitude)
        q = str(query or "").strip().lower()
        min_value = _float_or_none(min_price)
        max_value = _float_or_none(max_price)
        max_distance_value = _float_or_none(max_distance_km)

        filtered = []
        for doc in docs:
            searchable = " ".join(
                [
                    str(doc.get("name", "")),
                    str(doc.get("chef_name", "")),
                    " ".join(doc.get("tags", [])),
                ]
            ).lower()
            if q and q not in searchable:
                continue
            if featured == "true" and not doc["is_featured"]:
                continue
            if featured == "false" and doc["is_featured"]:
                continue
            if availability == "available" and not doc["is_available"]:
                continue
            if availability == "unavailable" and doc["is_available"]:
                continue
            if cuisine_type and doc["cuisine_type"] != cuisine_type:
                continue
            if diet_type and doc["diet_type"] != diet_type:
                continue
            if min_value is not None and doc["approx_price"] < min_value:
                continue
            if max_value is not None and doc["approx_price"] > max_value:
                continue
            if max_distance_value is not None:
                distance_km = doc.get("distance_km")
                if distance_km is None or distance_km > max_distance_value:
                    continue
            filtered.append(doc)

        if sort == "price_asc":
            filtered.sort(key=lambda d: d["approx_price"])
        elif sort == "price_desc":
            filtered.sort(key=lambda d: d["approx_price"], reverse=True)
        elif sort == "rating_desc":
            filtered.sort(key=lambda d: d["rating"], reverse=True)
        elif sort == "popular_desc":
            filtered.sort(key=lambda d: d["popularity"], reverse=True)
        elif sort == "distance_asc":
            filtered.sort(
                key=lambda d: (
                    d["distance_km"] is None,
                    d["distance_km"] if d["distance_km"] is not None else 0,
                )
            )
        else:
            filtered.sort(key=lambda d: (0 if d["is_featured"] else 1, d["name"].lower()))

        return filtered

    def _build_marketplace_docs(self, latitude: str = "", longitude: str = ""):
        client_lat = _float_or_none(latitude)
        client_lng = _float_or_none(longitude)
        active_menus = (
            DailyMenu.objects.filter(is_active=True)
            .select_related("chef", "chef__chef_profile", "chef__availability")
            .prefetch_related("items__dish")
        )
        result = []
        included_dish_ids = set()

        for menu in active_menus:
            for item in menu.items.select_related("dish").all():
                dish = item.dish
                if dish.status != "published" or dish.deleted_at is not None or dish.revision_status in ["oculta_temporalmente", "rechazada"]:
                    continue

                menu_status = str(item.status or "available")
                portions = int(item.portions or 0)
                result.append(
                    self._dish_doc(dish, menu=menu, menu_item=item, client_lat=client_lat, client_lng=client_lng)
                )
                included_dish_ids.add(dish.id)

        published_dishes = (
            Dish.objects.filter(status="published", deleted_at__isnull=True)
            .exclude(id__in=included_dish_ids)
            .exclude(revision_status__in=["oculta_temporalmente", "rechazada"])
            .select_related("chef", "chef__chef_profile", "chef__availability")
        )
        for dish in published_dishes:
            result.append(
                self._dish_doc(dish, client_lat=client_lat, client_lng=client_lng)
            )

        return result

    def _dish_doc(
        self,
        dish: Dish,
        menu: DailyMenu | None = None,
        menu_item=None,
        client_lat: float | None = None,
        client_lng: float | None = None,
    ):
        tags = _normalize_list(dish.tags)
        profile = _optional_related(dish.chef, "chef_profile")
        rating = _rating_for_chef(dish.chef)
        availability = _optional_related(dish.chef, "availability")
        snapshot = build_dish_stock_snapshot(
            dish=dish,
            menu=menu,
            menu_item=menu_item,
            availability=availability,
        )
        distance_km = _distance_km(
            client_lat,
            client_lng,
            getattr(profile, "location_latitude", None),
            getattr(profile, "location_longitude", None),
        )

        return {
            "id": dish.id,
            "name": dish.name,
            "image_url": dish.image_url,
            "approx_price": float(dish.price),
            "chef_name": _chef_name(dish.chef, profile),
            "is_featured": "DESTACADO" in tags or bool(menu and menu.is_active),
            "is_available": snapshot.is_available,
            "available_portions": snapshot.available_portions,
            "distance_km": distance_km,
            "rating": rating,
            "popularity": 0,
            "cuisine_type": _infer_cuisine(tags, profile),
            "diet_type": _infer_diet(tags),
            "tags": tags,
        }


def _optional_related(instance, related_name: str):
    try:
        return getattr(instance, related_name)
    except (AttributeError, ChefAvailability.DoesNotExist, ChefProfile.DoesNotExist):
        return None


def _chef_name(chef, profile):
    return (
        getattr(profile, "business_name", "")
        or f"{chef.first_name} {chef.last_name}".strip()
        or "Cocinero HomeChef"
    )


def _chef_is_available(chef):
    availability = _optional_related(chef, "availability")
    if availability is None:
        return False
    return is_open_now(
        {
            "is_active": availability.is_active,
            "weekly_schedule": availability.weekly_schedule,
        }
    )


def _normalize_list(value):
    if isinstance(value, list):
        return [str(item).strip().upper() for item in value if str(item).strip()]
    if not value:
        return []
    return [item.strip().upper() for item in str(value).split(",") if item.strip()]


def _infer_cuisine(tags, profile):
    joined = " ".join(tags + _normalize_list(getattr(profile, "specialties", [])))
    if "FUSION" in joined:
        return "fusion"
    if "INTERNACIONAL" in joined or "ITALIANA" in joined:
        return "internacional"
    if "VEG" in joined:
        return "veg"
    return "tradicional"


def _infer_diet(tags):
    if "VEGANO" in tags:
        return "vegano"
    if "VEGETARIANO" in tags:
        return "vegetariano"
    if "SIN_GLUTEN" in tags:
        return "sin_gluten"
    return "regular"


def _rating_for_chef(chef):
    chef_ids = [str(chef.supabase_user_id)]
    if chef.legacy_mongo_id:
        chef_ids.append(str(chef.legacy_mongo_id))
    reviews = MarketplaceReview.objects.filter(
        Q(chef=chef) | Q(chef_ref_id__in=chef_ids),
        dish__isnull=True,
        dish_ref_id="",
        is_public=True,
        deleted_at__isnull=True,
    )
    if not reviews.exists():
        return 0.0
    total = sum(review.rating for review in reviews)
    return round(total / reviews.count(), 2)


def _distance_km(origin_lat, origin_lng, destination_lat, destination_lng):
    if None in (origin_lat, origin_lng, destination_lat, destination_lng):
        return None

    origin_lat = float(origin_lat)
    origin_lng = float(origin_lng)
    destination_lat = float(destination_lat)
    destination_lng = float(destination_lng)
    earth_radius_km = 6371.0
    delta_lat = radians(destination_lat - origin_lat)
    delta_lng = radians(destination_lng - origin_lng)
    a = (
        sin(delta_lat / 2) ** 2
        + cos(radians(origin_lat)) * cos(radians(destination_lat)) * sin(delta_lng / 2) ** 2
    )
    c = 2 * asin(sqrt(a))
    distance = earth_radius_km * c
    if distance < 1:
        return round(distance, 3)
    return round(distance, 1)


def _float_or_none(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
