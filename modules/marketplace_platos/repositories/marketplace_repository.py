from uuid import UUID, uuid4

from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone

from modules.gestion_cocinero.models import ChefAvailability, ChefProfile, DailyMenu, Dish
from modules.gestion_cocinero.services.availability_rules import availability_summary, is_open_now
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.marketplace_platos.models import MarketplaceFavorite, MarketplacePreference, MarketplaceReview


class MarketplaceRepository:
    def __init__(self):
        pass

    def get_dish_detail(self, dish_id: str):
        dish = (
            Dish.objects.filter(id=dish_id, deleted_at__isnull=True)
            .select_related("chef", "chef__chef_profile", "chef__availability")
            .first()
        )
        if not dish or dish.status != "published":
            return None

        chef_id = str(dish.chef.supabase_user_id)
        menu = DailyMenu.objects.filter(chef=dish.chef, is_active=True).prefetch_related("items").first()
        menu_item = None
        if menu:
            menu_item = menu.items.filter(dish=dish).first()

        portions = int((menu_item.portions if menu_item else dish.portions) or 0)
        menu_status = str((menu_item.status if menu_item else "available") or "available")
        chef_available = _chef_is_available(dish.chef)
        is_available = menu_status in {"available", "published"} and portions > 0 and chef_available

        profile = _optional_related(dish.chef, "chef_profile")
        availability = _optional_related(dish.chef, "availability")
        chef_name = (
            getattr(profile, "business_name", "")
            or f"{dish.chef.first_name} {dish.chef.last_name}".strip()
            or "Cocinero HomeChef"
        )

        return {
            "id": dish.id,
            "name": dish.name,
            "description": dish.description or "Plato casero preparado con ingredientes frescos.",
            "image_url": dish.image_url,
            "gallery": [dish.image_url] if dish.image_url else [],
            "approx_price": float(dish.price),
            "ingredients": dish.ingredients or [],
            "tags": dish.tags or [],
            "allergens": dish.allergens,
            "available_portions": portions,
            "is_available": is_available,
            "schedule": (menu.schedule if menu else "") or dish.schedule or getattr(profile, "schedule", "") or "",
            "delivery_available": _chef_accepts_delivery(dish.chef),
            "chef": {
                "id": chef_id,
                "name": chef_name,
                "business_name": getattr(profile, "business_name", ""),
                "full_name": f"{dish.chef.first_name} {dish.chef.last_name}".strip(),
                "public_description": getattr(profile, "public_description", ""),
                "specialties": getattr(profile, "specialties", []) or [],
                "profile_image_url": getattr(profile, "profile_image_url", ""),
                "schedule": getattr(profile, "schedule", ""),
                "location": {
                    "latitude": getattr(profile, "location_latitude", None),
                    "longitude": getattr(profile, "location_longitude", None),
                    "address": getattr(profile, "location_address", ""),
                },
                "is_available": chef_available,
                "accept_delivery": bool(getattr(availability, "accept_delivery", True)),
                "accept_pickup": bool(getattr(availability, "accept_pickup", True)),
                "pickup_schedule": getattr(availability, "pickup_schedule", ""),
                "availability": _availability_payload(availability, chef_available),
            },
            "reputation": self.get_chef_reputation(chef_id),
            "chef_reputation": self.get_chef_reputation(chef_id),
            "dish_reviews": self.get_dish_reviews(dish.id),
        }

    def add_to_cart_stub(self, user_id: str, dish_id: str, quantity: int):
        dish = self.get_dish_detail(dish_id)
        if not dish or not dish["is_available"]:
            return {"ok": False, "code": "dish_unavailable"}
        if not dish["chef"]["is_available"]:
            return {"ok": False, "code": "chef_unavailable"}
        if quantity <= 0 or quantity > dish["available_portions"]:
            return {"ok": False, "code": "invalid_quantity", "available_portions": dish["available_portions"]}
        return {"ok": True, "message": "Plato agregado al carrito correctamente (stub CU-24)."}

    def list_favorites(self, user_id: str):
        user = self._require_user(user_id)
        return [
            {
                "_id": favorite.id,
                "id": favorite.id,
                "user_id": str(user.supabase_user_id),
                "favorite_type": favorite.favorite_type,
                "ref_id": favorite.ref_id,
                "target": self._favorite_target(favorite.favorite_type, favorite.ref_id),
                "created_at": favorite.created_at,
            }
            for favorite in MarketplaceFavorite.objects.filter(user=user, deleted_at__isnull=True).order_by("-created_at")
        ]

    def add_favorite(self, user_id: str, favorite_type: str, ref_id: str):
        user = self._require_user(user_id)
        try:
            favorite, created = MarketplaceFavorite.objects.get_or_create(
                user=user,
                favorite_type=favorite_type,
                ref_id=ref_id,
                defaults={"id": str(uuid4())},
            )
            if not created and favorite.deleted_at:
                favorite.deleted_at = None
                favorite.version += 1
                favorite.save(update_fields=["deleted_at", "version", "updated_at"])
                created = True
        except IntegrityError:
            created = False
        return {"duplicated": not created}

    def remove_favorite(self, user_id: str, favorite_type: str, ref_id: str):
        user = self._require_user(user_id)
        favorite = MarketplaceFavorite.objects.filter(
            user=user,
            favorite_type=favorite_type,
            ref_id=ref_id,
            deleted_at__isnull=True,
        ).first()
        if favorite:
            favorite.deleted_at = timezone.now()
            favorite.version += 1
            favorite.save(update_fields=["deleted_at", "version", "updated_at"])

    def get_preferences(self, user_id: str):
        user = self._require_user(user_id)
        preferences = MarketplacePreference.objects.filter(user=user, deleted_at__isnull=True).first()
        if not preferences:
            return {"user_id": user_id, "cuisine_types": [], "diet_types": [], "price_range": {}}
        return {
            "user_id": user_id,
            "cuisine_types": preferences.cuisine_types,
            "diet_types": preferences.diet_types,
            "price_range": preferences.price_range,
        }

    def save_preferences(self, user_id: str, payload: dict):
        user = self._require_user(user_id)
        current = MarketplacePreference.objects.filter(user=user).first()
        preferences, _ = MarketplacePreference.objects.update_or_create(
            user=user,
            defaults={
                "cuisine_types": payload.get("cuisine_types", []),
                "diet_types": payload.get("diet_types", []),
                "price_range": payload.get("price_range", {}),
                "deleted_at": None,
                "version": (current.version + 1) if current else 1,
            },
        )
        return {
            "user_id": user_id,
            "cuisine_types": preferences.cuisine_types,
            "diet_types": preferences.diet_types,
            "price_range": preferences.price_range,
        }

    def get_chef_reputation(self, chef_id: str):
        chef = self._find_chef_by_ref(chef_id)
        chef_refs = [str(chef_id)]
        if chef and chef.legacy_mongo_id:
            chef_refs.append(str(chef.legacy_mongo_id))
        query = Q(chef_ref_id__in=chef_refs, dish__isnull=True, dish_ref_id="")
        if chef:
            query |= Q(chef=chef, dish__isnull=True, dish_ref_id="")
        reviews = list(
            MarketplaceReview.objects.filter(query, is_public=True, deleted_at__isnull=True).order_by("-created_at")
        )
        if not reviews:
            return {
                "rating_avg": 0,
                "reviews_count": 0,
                "trust_level": "Sin reputación consolidada",
                "indicators": {
                    "consistency": "Sin historial suficiente",
                    "response_time": "Sin datos",
                    "cancellation_rate": "Sin datos",
                },
                "reviews": [],
            }
        avg = sum([review.rating for review in reviews]) / len(reviews)
        return {
            "rating_avg": round(avg, 2),
            "reviews_count": len(reviews),
            "trust_level": "Alto" if avg >= 4.5 else "Medio",
            "indicators": {
                "consistency": "Alta" if avg >= 4.5 else "En observación",
                "response_time": "Buena",
                "cancellation_rate": "Baja",
            },
            "reviews": [
                {
                    "id": review.id,
                    "author": review.author or "Cliente",
                    "rating": review.rating,
                    "comment": review.comment,
                    "created_at": review.created_at,
                }
                for review in reviews[:5]
            ],
        }

    def get_dish_reviews(self, dish_id: str):
        dish = (
            Dish.objects.filter(id=str(dish_id), status="published", deleted_at__isnull=True)
            .select_related("chef")
            .first()
        )
        dish_refs = [str(dish_id)]
        query = Q(dish_ref_id__in=dish_refs)
        if dish:
            query |= Q(dish=dish)
        reviews = list(
            MarketplaceReview.objects.filter(query, is_public=True, deleted_at__isnull=True).order_by("-created_at")
        )
        if not reviews:
            return {"rating_avg": 0, "reviews_count": 0, "reviews": []}
        avg = sum([review.rating for review in reviews]) / len(reviews)
        return {
            "rating_avg": round(avg, 2),
            "reviews_count": len(reviews),
            "reviews": [
                {
                    "id": review.id,
                    "author": review.author or "Cliente",
                    "rating": review.rating,
                    "comment": review.comment,
                    "created_at": review.created_at,
                }
                for review in reviews[:8]
            ],
        }

    def get_chef_public_profile(self, chef_id: str):
        chef = self._find_chef_by_ref(chef_id)
        if not chef:
            return None
        profile = _optional_related(chef, "chef_profile")
        availability = _optional_related(chef, "availability")
        dishes = Dish.objects.filter(chef=chef, status="published", deleted_at__isnull=True).order_by("-updated_at")
        return {
            "id": str(chef.supabase_user_id),
            "name": _chef_public_name(chef, profile),
            "full_name": f"{chef.first_name} {chef.last_name}".strip(),
            "public_description": getattr(profile, "public_description", ""),
            "specialties": getattr(profile, "specialties", []) or [],
            "profile_image_url": getattr(profile, "profile_image_url", ""),
            "schedule": getattr(profile, "schedule", ""),
            "location": {
                "latitude": getattr(profile, "location_latitude", None),
                "longitude": getattr(profile, "location_longitude", None),
                "address": getattr(profile, "location_address", ""),
            },
            "is_available": _chef_is_available(chef),
            "accept_delivery": bool(getattr(availability, "accept_delivery", True)),
            "accept_pickup": bool(getattr(availability, "accept_pickup", True)),
            "pickup_schedule": getattr(availability, "pickup_schedule", ""),
            "availability": _availability_payload(availability, _chef_is_available(chef)),
            "reputation": self.get_chef_reputation(str(chef.supabase_user_id)),
            "dishes": [
                {
                    "id": dish.id,
                    "name": dish.name,
                    "description": dish.description,
                    "image_url": dish.image_url,
                    "approx_price": float(dish.price),
                    "tags": dish.tags or [],
                    "is_available": int(dish.portions or 0) > 0 and _chef_is_available(chef),
                }
                for dish in dishes[:8]
            ],
        }

    def create_chef_review(self, user_id: str, chef_id: str, payload: dict):
        user = self._require_user(user_id)
        chef = self._find_chef_by_ref(chef_id)
        if not chef:
            raise ValueError("Cocinero no encontrado.")
        rating = int(payload.get("rating", 0))
        comment = str(payload.get("comment", "")).strip()
        if rating < 1 or rating > 5:
            raise ValueError("La calificación debe estar entre 1 y 5.")
        if len(comment) < 4:
            raise ValueError("La reseña debe tener al menos 4 caracteres.")
        if len(comment) > 600:
            raise ValueError("La reseña no puede superar 600 caracteres.")

        author = user.full_name or f"{user.first_name} {user.last_name}".strip() or "Cliente"
        review = MarketplaceReview.objects.create(
            id=str(uuid4()),
            chef=chef,
            chef_ref_id=str(chef.supabase_user_id),
            author=author,
            rating=rating,
            comment=comment,
            is_public=True,
        )
        return {
            "id": review.id,
            "author": review.author,
            "rating": review.rating,
            "comment": review.comment,
            "created_at": review.created_at,
        }

    def create_dish_review(self, user_id: str, dish_id: str, payload: dict):
        user = self._require_user(user_id)
        dish = (
            Dish.objects.filter(id=str(dish_id), status="published", deleted_at__isnull=True)
            .select_related("chef")
            .first()
        )
        if not dish:
            raise ValueError("Plato no encontrado.")
        rating = int(payload.get("rating", 0))
        comment = str(payload.get("comment", "")).strip()
        if rating < 1 or rating > 5:
            raise ValueError("La calificación debe estar entre 1 y 5.")
        if len(comment) < 4:
            raise ValueError("La reseña debe tener al menos 4 caracteres.")
        if len(comment) > 600:
            raise ValueError("La reseña no puede superar 600 caracteres.")

        author = user.full_name or f"{user.first_name} {user.last_name}".strip() or "Cliente"
        review = MarketplaceReview.objects.create(
            id=str(uuid4()),
            chef=dish.chef,
            chef_ref_id=str(dish.chef.supabase_user_id),
            dish=dish,
            dish_ref_id=str(dish.id),
            author=author,
            rating=rating,
            comment=comment,
            is_public=True,
        )
        return {
            "id": review.id,
            "author": review.author,
            "rating": review.rating,
            "comment": review.comment,
            "created_at": review.created_at,
        }

    def favorite_target_exists(self, favorite_type: str, ref_id: str):
        if favorite_type == MarketplaceFavorite.TYPE_DISH:
            return Dish.objects.filter(id=str(ref_id), status="published", deleted_at__isnull=True).exists()
        if favorite_type == MarketplaceFavorite.TYPE_CHEF:
            return self._find_chef_by_ref(ref_id) is not None
        return False

    def _require_user(self, user_id: str):
        try:
            parsed = UUID(str(user_id))
        except (TypeError, ValueError):
            parsed = None
        user = UserProfile.objects.filter(supabase_user_id=parsed).first() if parsed else None
        if not user:
            raise ValueError("Perfil de usuario no encontrado.")
        return user

    def _find_chef_by_ref(self, chef_id: str):
        parsed = _parse_uuid(chef_id)
        query = Q(role=UserProfile.ROLE_CHEF)
        ref_query = Q(legacy_mongo_id=str(chef_id))
        if parsed:
            ref_query |= Q(supabase_user_id=parsed)
        return UserProfile.objects.filter(query & ref_query).first()

    def _favorite_target(self, favorite_type: str, ref_id: str):
        if favorite_type == MarketplaceFavorite.TYPE_DISH:
            dish = (
                Dish.objects.filter(id=str(ref_id), deleted_at__isnull=True)
                .select_related("chef", "chef__chef_profile")
                .first()
            )
            if not dish:
                return None
            profile = _optional_related(dish.chef, "chef_profile")
            return {
                "id": dish.id,
                "name": dish.name,
                "image_url": dish.image_url,
                "approx_price": float(dish.price),
                "chef_id": str(dish.chef.supabase_user_id),
                "chef_name": _chef_public_name(dish.chef, profile),
                "status": dish.status,
            }

        if favorite_type == MarketplaceFavorite.TYPE_CHEF:
            chef = self._find_chef_by_ref(ref_id)
            if not chef:
                return None
            profile = _optional_related(chef, "chef_profile")
            availability = _optional_related(chef, "availability")
            return {
                "id": str(chef.supabase_user_id),
                "name": _chef_public_name(chef, profile),
                "full_name": f"{chef.first_name} {chef.last_name}".strip(),
                "profile_image_url": getattr(profile, "profile_image_url", ""),
                "public_description": getattr(profile, "public_description", ""),
                "specialties": getattr(profile, "specialties", []) or [],
                "is_available": _chef_is_available(chef),
                "accept_delivery": bool(getattr(availability, "accept_delivery", True)),
                "accept_pickup": bool(getattr(availability, "accept_pickup", True)),
            }

        return None


def _optional_related(instance, related_name: str):
    try:
        return getattr(instance, related_name)
    except (AttributeError, ChefAvailability.DoesNotExist, ChefProfile.DoesNotExist):
        return None


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


def _chef_accepts_delivery(chef):
    availability = _optional_related(chef, "availability")
    return False if availability is None else bool(availability.accept_delivery)


def _availability_payload(availability, is_available: bool):
    if availability is None:
        return {
            "is_active": False,
            "is_open_now": False,
            "weekly_schedule": [],
            "summary": "Sin horarios configurados",
            "status_label": "Sin disponibilidad configurada",
            "pickup_schedule": "",
            "accept_delivery": False,
            "accept_pickup": False,
            "simultaneous_orders_limit": 0,
        }
    data = {
        "is_active": availability.is_active,
        "weekly_schedule": availability.weekly_schedule or [],
    }
    return {
        "is_active": availability.is_active,
        "is_open_now": bool(is_available),
        "weekly_schedule": availability.weekly_schedule or [],
        "summary": availability_summary(data),
        "status_label": "Disponible ahora" if is_available else "Fuera de horario o pausado",
        "pickup_schedule": availability.pickup_schedule,
        "accept_delivery": availability.accept_delivery,
        "accept_pickup": availability.accept_pickup,
        "simultaneous_orders_limit": availability.simultaneous_orders_limit,
    }


def _chef_public_name(chef, profile):
    return (
        getattr(profile, "business_name", "")
        or f"{chef.first_name} {chef.last_name}".strip()
        or "Cocinero HomeChef"
    )


def _parse_uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
