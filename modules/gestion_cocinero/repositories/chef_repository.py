from datetime import datetime, timezone
from uuid import UUID, uuid4

from django.db import transaction

from modules.gestion_cocinero.models import ChefAvailability, ChefProfile, DailyMenu, DailyMenuItem, Dish
from modules.gestion_cocinero.services.availability_rules import availability_summary, is_open_now
from modules.gestion_usuarios_acceso_suscripcion.models import AuditEvent, UserProfile


class ChefRepository:
    def log(self, event: str, details: dict):
        AuditEvent.objects.create(event=event, details=details, at=datetime.now(timezone.utc))

    def get_profile(self, chef_id: str):
        user = self._find_user(chef_id)
        profile = ChefProfile.objects.filter(user=user).first() if user else None

        return {
            "user_id": chef_id,
            "first_name": user.first_name if user else "",
            "last_name": user.last_name if user else "",
            "email": user.email if user else "",
            "phone": user.phone if user else "",
            "business_name": profile.business_name if profile else "",
            "public_description": profile.public_description if profile else "",
            "specialties": profile.specialties if profile else [],
            "location": {
                "latitude": profile.location_latitude if profile else None,
                "longitude": profile.location_longitude if profile else None,
                "address": profile.location_address if profile else "",
            },
            "schedule": profile.schedule if profile else "",
            "profile_image_url": profile.profile_image_url if profile else "",
            "status": profile.status if profile else "pending_validation",
            "updated_at": profile.updated_at if profile else None,
            "ai_subscription_active": profile.ai_subscription_active if profile else False,
        }

    def save_profile(self, chef_id: str, payload: dict):
        user = self._require_user(chef_id)
        current = ChefProfile.objects.filter(user=user).first()
        current_location = {
            "latitude": current.location_latitude if current else None,
            "longitude": current.location_longitude if current else None,
            "address": current.location_address if current else "",
        }
        location = payload.get("location") or current_location
        specialties = payload.get("specialties", current.specialties if current else [])
        if isinstance(specialties, str):
            specialties = [item.strip() for item in specialties.split(",") if item.strip()]

        defaults = {
            "business_name": str(payload.get("business_name", current.business_name if current else "")).strip(),
            "public_description": str(
                payload.get("public_description", current.public_description if current else "")
            ).strip(),
            "specialties": specialties,
            "location_latitude": location.get("latitude"),
            "location_longitude": location.get("longitude"),
            "location_address": str(location.get("address", "")).strip(),
            "schedule": str(payload.get("schedule", current.schedule if current else "")).strip(),
            "profile_image_url": str(payload.get("profile_image_url", current.profile_image_url if current else "")).strip(),
            "status": str(payload.get("status") or (current.status if current else "pending_validation")).strip(),
        }
        ChefProfile.objects.update_or_create(user=user, defaults=defaults)
        return self.get_profile(chef_id)

    def save_location(self, chef_id: str, payload: dict):
        user = self._require_user(chef_id)
        ChefProfile.objects.update_or_create(
            user=user,
            defaults={
                "location_latitude": payload.get("latitude"),
                "location_longitude": payload.get("longitude"),
                "location_address": str(payload.get("address", "")).strip(),
            },
        )
        return self.get_profile(chef_id)

    def update_user_identity(self, chef_id: str, payload: dict):
        user = self._require_user(chef_id)
        changed = []
        for field in ("first_name", "last_name", "phone"):
            if field in payload and getattr(user, field) != payload[field]:
                setattr(user, field, payload[field])
                changed.append(field)
        if changed:
            user.full_name = f"{user.first_name} {user.last_name}".strip()
            user.save(update_fields=[*changed, "full_name", "updated_at"])

    def get_availability(self, chef_id: str):
        user = self._find_user(chef_id)
        availability = ChefAvailability.objects.filter(chef=user).first() if user else None
        if not availability:
            data = {
                "chef_id": chef_id,
                "is_active": True,
                "weekly_schedule": [],
                "pickup_schedule": "",
                "accept_delivery": True,
                "accept_pickup": True,
                "simultaneous_orders_limit": 10,
            }
            return self._availability_defaults(data)
        return self._availability_to_dict(availability)

    def save_availability(self, chef_id: str, payload: dict):
        user = self._require_user(chef_id)
        availability, _ = ChefAvailability.objects.update_or_create(
            chef=user,
            defaults={
                "is_active": bool(payload.get("is_active", True)),
                "weekly_schedule": payload.get("weekly_schedule", []),
                "pickup_schedule": str(payload.get("pickup_schedule", "")).strip(),
                "accept_delivery": bool(payload.get("accept_delivery", True)),
                "accept_pickup": bool(payload.get("accept_pickup", True)),
                "simultaneous_orders_limit": int(payload.get("simultaneous_orders_limit", 10)),
            },
        )
        return self._availability_to_dict(availability)

    def has_active_orders(self, chef_id: str):
        return False

    def list_dishes(self, chef_id: str):
        user = self._require_user(chef_id)
        return [self._dish_to_dict(dish) for dish in Dish.objects.filter(chef=user).order_by("-updated_at")]

    def get_dish(self, chef_id: str, dish_id: str):
        user = self._require_user(chef_id)
        dish = Dish.objects.filter(id=dish_id, chef=user).first()
        return self._dish_to_dict(dish) if dish else None

    def save_dish(self, chef_id: str, payload: dict):
        user = self._require_user(chef_id)
        dish_id = payload.get("_id") or payload.get("id") or str(uuid4())
        action = payload.get("action", "draft")
        status = "published" if action == "publish" else payload.get("status", "draft")
        if status not in {"published", "paused", "draft", "sold_out"}:
            raise ValueError("Estado de publicacion invalido.")
        dish, _ = Dish.objects.update_or_create(
            id=str(dish_id),
            chef=user,
            defaults={
                "name": str(payload["name"]).strip(),
                "description": str(payload.get("description", "")).strip(),
                "price": payload["price"],
                "portions": int(payload["portions"]),
                "ingredients": payload.get("ingredients", []),
                "tags": payload.get("tags", []),
                "allergens": payload.get("allergens", []),
                "image_url": str(payload.get("image_url", "")).strip(),
                "schedule": str(payload.get("schedule", "")).strip(),
                "status": status,
            },
        )
        return self._dish_to_dict(dish)

    def update_dish_status(self, chef_id: str, dish_id: str, status: str):
        user = self._require_user(chef_id)
        dish = Dish.objects.filter(id=dish_id, chef=user).first()
        if not dish:
            return None
        dish.status = status
        dish.save(update_fields=["status", "updated_at"])
        return self._dish_to_dict(dish)

    def delete_dish(self, chef_id: str, dish_id: str):
        user = self._require_user(chef_id)
        Dish.objects.filter(id=dish_id, chef=user).delete()

    def get_daily_menu(self, chef_id: str):
        user = self._find_user(chef_id)
        menu = DailyMenu.objects.filter(chef=user).prefetch_related("items__dish").first() if user else None
        if not menu:
            return {"chef_id": chef_id, "items": [], "is_active": False, "schedule": ""}
        return self._menu_to_dict(menu)

    @transaction.atomic
    def save_daily_menu(self, chef_id: str, payload: dict):
        user = self._require_user(chef_id)
        menu, _ = DailyMenu.objects.update_or_create(
            chef=user,
            defaults={
                "schedule": str(payload.get("schedule", "")).strip(),
                "is_active": bool(payload.get("is_active", False)),
            },
        )
        menu.items.all().delete()
        for index, item in enumerate(payload.get("items", [])):
            dish_id = str(item.get("dish_id") or item.get("id") or "")
            dish = Dish.objects.filter(id=dish_id, chef=user).first()
            if not dish:
                raise ValueError(f"Plato no encontrado en el menu: {dish_id}")
            DailyMenuItem.objects.create(
                menu=menu,
                dish=dish,
                portions=int(item.get("portions", 0)),
                status=str(item.get("status", "available")),
                sort_order=index,
            )
        return self.get_daily_menu(chef_id)

    def dashboard_metrics(self, chef_id: str):
        user = self._require_user(chef_id)
        dishes = list(Dish.objects.filter(chef=user))
        published = [dish for dish in dishes if dish.status == "published"]
        availability = self.get_availability(chef_id)
        return {
            "sales_total": 0,
            "income_total": 0,
            "commissions_total": 0,
            "orders_recent": [],
            "dishes_total": len(dishes),
            "dishes_published": len(published),
            "kitchen_status": "active" if availability.get("is_open_now") else "paused",
            "suggestions": ["Publica tu menu del dia", "Actualiza porciones para platos destacados"],
        }

    def _find_user(self, user_id: str):
        try:
            parsed = UUID(str(user_id))
        except (TypeError, ValueError):
            return None
        return UserProfile.objects.filter(supabase_user_id=parsed).first()

    def _require_user(self, user_id: str):
        user = self._find_user(user_id)
        if not user:
            raise ValueError("Perfil de usuario no encontrado.")
        return user

    def _availability_to_dict(self, availability: ChefAvailability):
        data = {
            "chef_id": str(availability.chef.supabase_user_id),
            "is_active": availability.is_active,
            "weekly_schedule": availability.weekly_schedule,
            "pickup_schedule": availability.pickup_schedule,
            "accept_delivery": availability.accept_delivery,
            "accept_pickup": availability.accept_pickup,
            "simultaneous_orders_limit": availability.simultaneous_orders_limit,
        }
        return self._availability_defaults(data)

    def _availability_defaults(self, data: dict):
        data["is_open_now"] = is_open_now(data)
        data["summary"] = availability_summary(data)
        data["status_label"] = "Disponible ahora" if data["is_open_now"] else "Fuera de horario o pausado"
        return data

    def _dish_to_dict(self, dish: Dish):
        return {
            "_id": dish.id,
            "id": dish.id,
            "chef_id": str(dish.chef.supabase_user_id),
            "name": dish.name,
            "description": dish.description,
            "price": float(dish.price),
            "portions": dish.portions,
            "ingredients": dish.ingredients,
            "tags": dish.tags,
            "allergens": dish.allergens,
            "image_url": dish.image_url,
            "schedule": dish.schedule,
            "status": dish.status,
            "updated_at": dish.updated_at,
        }

    def _menu_to_dict(self, menu: DailyMenu):
        return {
            "id": menu.id,
            "chef_id": str(menu.chef.supabase_user_id),
            "is_active": menu.is_active,
            "schedule": menu.schedule,
            "items": [
                {
                    "dish_id": item.dish_id,
                    "name": item.dish.name,
                    "portions": item.portions,
                    "status": item.status,
                }
                for item in menu.items.select_related("dish").all()
            ],
            "updated_at": menu.updated_at,
        }
