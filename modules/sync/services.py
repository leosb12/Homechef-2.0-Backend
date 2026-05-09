from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from modules.gestion_cocinero.models import ChefAvailability, ChefProfile, DailyMenu, DailyMenuItem, Dish
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.marketplace_platos.models import MarketplaceFavorite, MarketplacePreference, MarketplaceReview
from modules.sync.models import SyncOperation
from modules.sync.repositories import SyncRepository


SYNCABLE_ENTITIES = {
    "dishes": "dishes",
    "platos": "dishes",
    "chef_profiles": "chef_profiles",
    "perfil_cocinero": "chef_profiles",
    "chef_availability": "chef_availability",
    "disponibilidad_cocinero": "chef_availability",
    "daily_menus": "daily_menus",
    "menus_diarios": "daily_menus",
    "favorites": "favorites",
    "favoritos": "favorites",
    "preferences": "preferences",
    "preferencias": "preferences",
    "reviews": "reviews",
    "resenas": "reviews",
}


class SyncService:
    def __init__(self):
        self.repo = SyncRepository()

    def sync_operations(self, user, payload: dict):
        profile = self._require_profile(user)
        device_id = payload["device_id"]
        last_sync = payload.get("last_sync")
        synced = []
        errors = []
        conflicts = []

        for operation in payload.get("operations", []):
            result = self._process_operation(profile, device_id, last_sync, operation)
            if result["status"] == SyncOperation.STATUS_SYNCED:
                synced.append(result["data"])
            elif result["status"] == SyncOperation.STATUS_CONFLICT:
                conflicts.append(result["data"])
            else:
                errors.append(result["data"])

        return {
            "synced": synced,
            "errors": errors,
            "conflicts": conflicts,
            "server_time": timezone.now(),
        }

    def get_changes(self, user, last_sync):
        profile = self._require_profile(user)
        changes = {
            "dishes": [self._dish_to_sync_dict(item) for item in self._changed_dishes(profile, last_sync)],
            "chef_profiles": [
                self._chef_profile_to_sync_dict(item) for item in self._changed_chef_profiles(profile, last_sync)
            ],
            "chef_availability": [
                self._availability_to_sync_dict(item) for item in self._changed_availability(profile, last_sync)
            ],
            "daily_menus": [self._daily_menu_to_sync_dict(item) for item in self._changed_daily_menus(profile, last_sync)],
            "favorites": [self._favorite_to_sync_dict(item) for item in self._changed_favorites(profile, last_sync)],
            "preferences": [self._preference_to_sync_dict(item) for item in self._changed_preferences(profile, last_sync)],
            "reviews": [self._review_to_sync_dict(item) for item in self._changed_reviews(profile, last_sync)],
        }
        return {
            "changes": changes,
            "deleted": {
                entity: [item for item in items if item.get("deleted_at")]
                for entity, items in changes.items()
            },
            "server_time": timezone.now(),
        }

    def _process_operation(self, profile: UserProfile, device_id: str, last_sync, operation: dict):
        operation_id = str(operation["operation_id"])
        previous = self.repo.find_operation(operation_id)
        if previous:
            return {"status": previous.status, "data": previous.result}

        entity = str(operation["entity"]).strip().lower()
        canonical_entity = SYNCABLE_ENTITIES.get(entity)
        if not canonical_entity:
            return self._record_error(device_id, operation, "Entidad no autorizada para sincronizacion.")

        try:
            with transaction.atomic():
                result = self._apply_operation(profile, canonical_entity, operation, last_sync)
                self.repo.record_operation(
                    operation_id=operation_id,
                    device_id=device_id,
                    entity=canonical_entity,
                    action=operation["action"],
                    local_id=str(operation.get("local_id") or ""),
                    server_id=str(result.get("server_id") or ""),
                    status=result["status"],
                    result=result,
                    processed_at=timezone.now(),
                )
                return {"status": result["status"], "data": result}
        except ValueError as exc:
            return self._record_error(device_id, operation, str(exc))

    def _apply_operation(self, profile: UserProfile, entity: str, operation: dict, last_sync):
        handlers = {
            "dishes": self._apply_dish_operation,
            "chef_profiles": self._apply_chef_profile_operation,
            "chef_availability": self._apply_availability_operation,
            "daily_menus": self._apply_daily_menu_operation,
            "favorites": self._apply_favorite_operation,
            "preferences": self._apply_preference_operation,
            "reviews": self._apply_review_operation,
        }
        return handlers[entity](profile, operation, last_sync)

    def _apply_dish_operation(self, profile: UserProfile, operation: dict, last_sync):
        action = operation["action"]
        if action == "CREATE":
            payload = operation.get("payload") or {}
            self._validate_dish_payload(payload)
            dish_id = str(operation.get("server_id") or payload.get("id") or payload.get("_id") or "")
            existing = self._get_owned(Dish, profile, dish_id, owner_field="chef") if dish_id else None
            if existing:
                return self._synced_result(operation, existing)
            dish = Dish.objects.create(
                chef=profile,
                name=str(payload["name"]).strip(),
                description=str(payload.get("description", "")).strip(),
                price=payload["price"],
                portions=int(payload.get("portions", 1)),
                ingredients=payload.get("ingredients", []),
                tags=payload.get("tags", []),
                allergens=payload.get("allergens", []),
                image_url=str(payload.get("image_url", "")).strip(),
                schedule=str(payload.get("schedule", "")).strip(),
                status=str(payload.get("status", Dish.STATUS_DRAFT)).strip() or Dish.STATUS_DRAFT,
                version=1,
            )
            return self._synced_result(operation, dish)

        dish = self._require_owned(Dish, profile, operation.get("server_id"), "Plato", owner_field="chef")
        conflict = self._detect_conflict(dish, operation, last_sync, self._dish_to_sync_dict)
        if conflict:
            return conflict
        if action == "UPDATE":
            payload = operation.get("payload") or {}
            self._validate_dish_payload(payload, partial=True)
            self._assign_fields(
                dish,
                payload,
                ("name", "description", "price", "portions", "ingredients", "tags", "allergens", "image_url", "schedule", "status"),
            )
            return self._save_synced(operation, dish)
        if action == "DELETE":
            return self._soft_delete_synced(operation, dish)
        raise ValueError("Accion no soportada.")

    def _apply_chef_profile_operation(self, profile: UserProfile, operation: dict, last_sync):
        action = operation["action"]
        item = self._one_to_one_item(ChefProfile, profile, "user", operation)
        if action in {"UPDATE", "DELETE"} and not item:
            raise ValueError("Perfil de cocinero no encontrado.")
        if item:
            conflict = self._detect_conflict(item, operation, last_sync, self._chef_profile_to_sync_dict)
            if conflict:
                return conflict

        if action in {"CREATE", "UPDATE"}:
            payload = operation.get("payload") or {}
            if not item:
                item = ChefProfile(user=profile)
            location = payload.get("location") or {}
            self._assign_fields(
                item,
                payload,
                ("business_name", "public_description", "specialties", "schedule", "profile_image_url", "status"),
            )
            if "latitude" in location:
                item.location_latitude = location.get("latitude")
            if "longitude" in location:
                item.location_longitude = location.get("longitude")
            if "address" in location:
                item.location_address = str(location.get("address") or "").strip()
            return self._save_synced(operation, item)
        if action == "DELETE":
            return self._soft_delete_synced(operation, item)
        raise ValueError("Accion no soportada.")

    def _apply_availability_operation(self, profile: UserProfile, operation: dict, last_sync):
        action = operation["action"]
        item = self._one_to_one_item(ChefAvailability, profile, "chef", operation)
        if action in {"UPDATE", "DELETE"} and not item:
            raise ValueError("Disponibilidad de cocinero no encontrada.")
        if item:
            conflict = self._detect_conflict(item, operation, last_sync, self._availability_to_sync_dict)
            if conflict:
                return conflict

        if action in {"CREATE", "UPDATE"}:
            payload = operation.get("payload") or {}
            if not item:
                item = ChefAvailability(chef=profile)
            self._assign_fields(
                item,
                payload,
                (
                    "is_active",
                    "weekly_schedule",
                    "pickup_schedule",
                    "accept_delivery",
                    "accept_pickup",
                    "simultaneous_orders_limit",
                ),
            )
            return self._save_synced(operation, item)
        if action == "DELETE":
            return self._soft_delete_synced(operation, item)
        raise ValueError("Accion no soportada.")

    def _apply_daily_menu_operation(self, profile: UserProfile, operation: dict, last_sync):
        action = operation["action"]
        item = self._one_to_one_item(DailyMenu, profile, "chef", operation)
        if action in {"UPDATE", "DELETE"} and not item:
            raise ValueError("Menu diario no encontrado.")
        if item:
            conflict = self._detect_conflict(item, operation, last_sync, self._daily_menu_to_sync_dict)
            if conflict:
                return conflict

        if action in {"CREATE", "UPDATE"}:
            payload = operation.get("payload") or {}
            if not item:
                item = DailyMenu(chef=profile)
            self._assign_fields(item, payload, ("schedule", "is_active"))
            saved = self._save_model(item)
            self._replace_menu_items(saved, payload.get("items", []))
            return self._synced_result(operation, saved)
        if action == "DELETE":
            return self._soft_delete_synced(operation, item)
        raise ValueError("Accion no soportada.")

    def _apply_favorite_operation(self, profile: UserProfile, operation: dict, last_sync):
        action = operation["action"]
        payload = operation.get("payload") or {}
        item = self._get_owned(MarketplaceFavorite, profile, operation.get("server_id"), owner_field="user")
        if not item and payload.get("favorite_type") and payload.get("ref_id"):
            item = MarketplaceFavorite.objects.filter(
                user=profile,
                favorite_type=str(payload.get("favorite_type")),
                ref_id=str(payload.get("ref_id")),
            ).first()
        if action in {"UPDATE", "DELETE"} and not item:
            raise ValueError("Favorito no encontrado.")
        if item:
            conflict = self._detect_conflict(item, operation, last_sync, self._favorite_to_sync_dict)
            if conflict:
                return conflict

        if action in {"CREATE", "UPDATE"}:
            if not item:
                item = MarketplaceFavorite(user=profile)
            self._assign_fields(item, payload, ("favorite_type", "ref_id"))
            if not item.favorite_type or not item.ref_id:
                raise ValueError("favorite_type y ref_id son obligatorios.")
            return self._save_synced(operation, item)
        if action == "DELETE":
            return self._soft_delete_synced(operation, item)
        raise ValueError("Accion no soportada.")

    def _apply_preference_operation(self, profile: UserProfile, operation: dict, last_sync):
        action = operation["action"]
        item = self._one_to_one_item(MarketplacePreference, profile, "user", operation)
        if action in {"UPDATE", "DELETE"} and not item:
            raise ValueError("Preferencias no encontradas.")
        if item:
            conflict = self._detect_conflict(item, operation, last_sync, self._preference_to_sync_dict)
            if conflict:
                return conflict

        if action in {"CREATE", "UPDATE"}:
            payload = operation.get("payload") or {}
            if not item:
                item = MarketplacePreference(user=profile)
            self._assign_fields(item, payload, ("cuisine_types", "diet_types", "price_range"))
            return self._save_synced(operation, item)
        if action == "DELETE":
            return self._soft_delete_synced(operation, item)
        raise ValueError("Accion no soportada.")

    def _apply_review_operation(self, profile: UserProfile, operation: dict, last_sync):
        action = operation["action"]
        payload = operation.get("payload") or {}
        item = self._get_review_for_user(profile, operation.get("server_id"))
        if action in {"UPDATE", "DELETE"} and not item:
            raise ValueError("Resena no encontrada o no editable por el usuario.")
        if item:
            conflict = self._detect_conflict(item, operation, last_sync, self._review_to_sync_dict)
            if conflict:
                return conflict

        if action in {"CREATE", "UPDATE"}:
            if not item:
                item = MarketplaceReview(author=self._review_author(profile))
            self._assign_fields(item, payload, ("rating", "comment", "is_public"))
            self._bind_review_target(item, payload)
            self._validate_review(item)
            return self._save_synced(operation, item)
        if action == "DELETE":
            return self._soft_delete_synced(operation, item)
        raise ValueError("Accion no soportada.")

    def _detect_conflict(self, item, operation: dict, last_sync, serializer):
        client_version = operation.get("version")
        server_updated_at = getattr(item, "updated_at", None) or getattr(item, "created_at", None)
        server_newer_than_last_sync = last_sync and server_updated_at and server_updated_at > last_sync
        server_version = getattr(item, "version", None)
        server_version_newer = client_version is not None and server_version is not None and server_version > int(client_version)
        if server_newer_than_last_sync or server_version_newer:
            return {
                "operation_id": str(operation["operation_id"]),
                "entity": operation["entity"],
                "local_id": operation.get("local_id") or "",
                "server_id": self._server_id(item),
                "status": SyncOperation.STATUS_CONFLICT,
                "reason": "SERVER_VERSION_NEWER",
                "server_data": serializer(item),
                "client_data": operation.get("payload") or {},
            }
        return None

    def _record_error(self, device_id: str, operation: dict, message: str):
        result = {
            "operation_id": str(operation.get("operation_id") or ""),
            "entity": operation.get("entity") or "",
            "local_id": operation.get("local_id") or "",
            "server_id": operation.get("server_id"),
            "status": SyncOperation.STATUS_ERROR,
            "error": message,
        }
        if operation.get("operation_id") and not self.repo.find_operation(str(operation["operation_id"])):
            self.repo.record_operation(
                operation_id=str(operation["operation_id"]),
                device_id=device_id,
                entity=str(operation.get("entity") or ""),
                action=str(operation.get("action") or ""),
                local_id=str(operation.get("local_id") or ""),
                server_id=str(operation.get("server_id") or ""),
                status=SyncOperation.STATUS_ERROR,
                result=result,
                error_message=message,
                processed_at=timezone.now(),
            )
        return {"status": SyncOperation.STATUS_ERROR, "data": result}

    def _save_synced(self, operation: dict, item):
        saved = self._save_model(item)
        return self._synced_result(operation, saved)

    def _soft_delete_synced(self, operation: dict, item):
        item.deleted_at = timezone.now()
        saved = self._save_model(item)
        return self._synced_result(operation, saved)

    def _save_model(self, item):
        if hasattr(item, "deleted_at") and item.deleted_at is None:
            item.deleted_at = None
        if hasattr(item, "version"):
            item.version = int(item.version or 1) if item._state.adding else int(item.version or 0) + 1
        item.save()
        return item

    def _synced_result(self, operation: dict, item):
        return {
            "operation_id": str(operation["operation_id"]),
            "entity": operation["entity"],
            "local_id": operation.get("local_id") or "",
            "server_id": self._server_id(item),
            "status": SyncOperation.STATUS_SYNCED,
            "version": getattr(item, "version", None),
            "updated_at": self._iso(getattr(item, "updated_at", None) or getattr(item, "created_at", None)),
        }

    def _one_to_one_item(self, model, profile: UserProfile, owner_field: str, operation: dict):
        item = self._get_owned(model, profile, operation.get("server_id"), owner_field=owner_field)
        if item:
            return item
        return model.objects.filter(**{owner_field: profile}).first()

    def _get_owned(self, model, profile: UserProfile, item_id, owner_field: str):
        if not item_id:
            return None
        return model.objects.filter(id=str(item_id), **{owner_field: profile}).first()

    def _require_owned(self, model, profile: UserProfile, item_id, label: str, owner_field: str):
        item = self._get_owned(model, profile, item_id, owner_field)
        if not item:
            raise ValueError(f"{label} no encontrado o no pertenece al usuario.")
        return item

    def _assign_fields(self, item, payload: dict, fields):
        for field in fields:
            if field in payload:
                value = payload[field]
                if isinstance(value, str):
                    value = value.strip()
                setattr(item, field, value)
        if hasattr(item, "deleted_at"):
            item.deleted_at = None

    def _replace_menu_items(self, menu: DailyMenu, items):
        if items is None:
            return
        menu.items.all().delete()
        for index, item in enumerate(items or []):
            dish_id = str(item.get("dish_id") or item.get("id") or "")
            dish = Dish.objects.filter(id=dish_id, chef=menu.chef, deleted_at__isnull=True).first()
            if not dish:
                raise ValueError(f"Plato no encontrado en el menu: {dish_id}")
            DailyMenuItem.objects.create(
                menu=menu,
                dish=dish,
                portions=int(item.get("portions", 1)),
                status=str(item.get("status", "available")),
                sort_order=index,
            )

    def _bind_review_target(self, review: MarketplaceReview, payload: dict):
        dish_id = payload.get("dish_id") or payload.get("dish_ref_id")
        chef_id = payload.get("chef_id") or payload.get("chef_ref_id")
        if dish_id:
            dish = Dish.objects.filter(id=str(dish_id), deleted_at__isnull=True).select_related("chef").first()
            if not dish:
                raise ValueError("Plato no encontrado para la resena.")
            review.dish = dish
            review.dish_ref_id = str(dish.id)
            review.chef = dish.chef
            review.chef_ref_id = str(dish.chef.supabase_user_id)
            return
        if chef_id:
            chef = UserProfile.objects.filter(supabase_user_id=chef_id, role=UserProfile.ROLE_CHEF).first()
            if not chef:
                raise ValueError("Cocinero no encontrado para la resena.")
            review.chef = chef
            review.chef_ref_id = str(chef.supabase_user_id)
            review.dish = None
            review.dish_ref_id = ""
            return
        if not review.chef_ref_id and not review.dish_ref_id:
            raise ValueError("La resena requiere dish_id o chef_id.")

    def _validate_review(self, review: MarketplaceReview):
        rating = int(review.rating or 0)
        comment = str(review.comment or "").strip()
        if rating < 1 or rating > 5:
            raise ValueError("La calificacion debe estar entre 1 y 5.")
        if len(comment) < 4:
            raise ValueError("La resena debe tener al menos 4 caracteres.")
        if len(comment) > 600:
            raise ValueError("La resena no puede superar 600 caracteres.")

    def _get_review_for_user(self, profile: UserProfile, review_id):
        if not review_id:
            return None
        author = self._review_author(profile)
        return MarketplaceReview.objects.filter(id=str(review_id), author=author).first()

    def _review_author(self, profile: UserProfile):
        return profile.full_name or f"{profile.first_name} {profile.last_name}".strip() or "Cliente"

    def _require_profile(self, user):
        profile = getattr(user, "profile", None)
        if not profile:
            raise ValueError("Perfil de usuario no encontrado.")
        return profile

    def _validate_dish_payload(self, payload: dict, partial: bool = False):
        if not partial or "name" in payload:
            if not str(payload.get("name", "")).strip():
                raise ValueError("Nombre del plato es obligatorio.")
        if not partial or "price" in payload:
            try:
                price = float(payload.get("price", 0))
            except (TypeError, ValueError):
                price = 0
            if price <= 0:
                raise ValueError("Precio invalido.")
        if not partial or "portions" in payload:
            try:
                portions = int(payload.get("portions", 0))
            except (TypeError, ValueError):
                portions = 0
            if portions <= 0:
                raise ValueError("Porciones invalidas.")

    def _changed_dishes(self, profile, last_sync):
        return self._changed(Dish.objects.filter(chef=profile), last_sync)

    def _changed_chef_profiles(self, profile, last_sync):
        return self._changed(ChefProfile.objects.filter(user=profile), last_sync)

    def _changed_availability(self, profile, last_sync):
        return self._changed(ChefAvailability.objects.filter(chef=profile), last_sync)

    def _changed_daily_menus(self, profile, last_sync):
        return self._changed(DailyMenu.objects.filter(chef=profile), last_sync).prefetch_related("items__dish")

    def _changed_favorites(self, profile, last_sync):
        return self._changed(MarketplaceFavorite.objects.filter(user=profile), last_sync)

    def _changed_preferences(self, profile, last_sync):
        return self._changed(MarketplacePreference.objects.filter(user=profile), last_sync)

    def _changed_reviews(self, profile, last_sync):
        author = self._review_author(profile)
        query = Q(author=author)
        if profile.role == UserProfile.ROLE_CHEF:
            query |= Q(chef=profile) | Q(chef_ref_id=str(profile.supabase_user_id))
        return self._changed(MarketplaceReview.objects.filter(query), last_sync)

    def _changed(self, queryset, last_sync):
        if not last_sync:
            return queryset.order_by("updated_at" if self._has_field(queryset.model, "updated_at") else "created_at")
        updated_field = "updated_at" if self._has_field(queryset.model, "updated_at") else "created_at"
        query = Q(**{f"{updated_field}__gt": last_sync})
        if self._has_field(queryset.model, "deleted_at"):
            query |= Q(deleted_at__gt=last_sync)
        return queryset.filter(query).order_by(updated_field)

    def _has_field(self, model, field_name: str):
        return any(field.name == field_name for field in model._meta.fields)

    def _dish_to_sync_dict(self, dish: Dish):
        return {
            "id": dish.id,
            "_id": dish.id,
            "entity": "dishes",
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
            "version": dish.version,
            "created_at": self._iso(dish.created_at),
            "updated_at": self._iso(dish.updated_at),
            "deleted_at": self._iso(dish.deleted_at),
        }

    def _chef_profile_to_sync_dict(self, profile: ChefProfile):
        return {
            "id": profile.id,
            "entity": "chef_profiles",
            "user_id": str(profile.user.supabase_user_id),
            "business_name": profile.business_name,
            "public_description": profile.public_description,
            "specialties": profile.specialties,
            "location": {
                "latitude": profile.location_latitude,
                "longitude": profile.location_longitude,
                "address": profile.location_address,
            },
            "schedule": profile.schedule,
            "profile_image_url": profile.profile_image_url,
            "status": profile.status,
            "version": profile.version,
            "created_at": self._iso(profile.created_at),
            "updated_at": self._iso(profile.updated_at),
            "deleted_at": self._iso(profile.deleted_at),
        }

    def _availability_to_sync_dict(self, availability: ChefAvailability):
        return {
            "id": availability.id,
            "entity": "chef_availability",
            "chef_id": str(availability.chef.supabase_user_id),
            "is_active": availability.is_active,
            "weekly_schedule": availability.weekly_schedule,
            "pickup_schedule": availability.pickup_schedule,
            "accept_delivery": availability.accept_delivery,
            "accept_pickup": availability.accept_pickup,
            "simultaneous_orders_limit": availability.simultaneous_orders_limit,
            "version": availability.version,
            "created_at": self._iso(availability.created_at),
            "updated_at": self._iso(availability.updated_at),
            "deleted_at": self._iso(availability.deleted_at),
        }

    def _daily_menu_to_sync_dict(self, menu: DailyMenu):
        return {
            "id": menu.id,
            "entity": "daily_menus",
            "chef_id": str(menu.chef.supabase_user_id),
            "schedule": menu.schedule,
            "is_active": menu.is_active,
            "items": [
                {
                    "dish_id": item.dish_id,
                    "name": item.dish.name,
                    "portions": item.portions,
                    "status": item.status,
                    "sort_order": item.sort_order,
                }
                for item in menu.items.select_related("dish").all()
            ],
            "version": menu.version,
            "created_at": self._iso(menu.created_at),
            "updated_at": self._iso(menu.updated_at),
            "deleted_at": self._iso(menu.deleted_at),
        }

    def _favorite_to_sync_dict(self, favorite: MarketplaceFavorite):
        return {
            "id": favorite.id,
            "entity": "favorites",
            "user_id": str(favorite.user.supabase_user_id),
            "favorite_type": favorite.favorite_type,
            "ref_id": favorite.ref_id,
            "version": favorite.version,
            "created_at": self._iso(favorite.created_at),
            "updated_at": self._iso(favorite.updated_at),
            "deleted_at": self._iso(favorite.deleted_at),
        }

    def _preference_to_sync_dict(self, preference: MarketplacePreference):
        return {
            "id": preference.id,
            "entity": "preferences",
            "user_id": str(preference.user.supabase_user_id),
            "cuisine_types": preference.cuisine_types,
            "diet_types": preference.diet_types,
            "price_range": preference.price_range,
            "version": preference.version,
            "created_at": self._iso(preference.created_at),
            "updated_at": self._iso(preference.updated_at),
            "deleted_at": self._iso(preference.deleted_at),
        }

    def _review_to_sync_dict(self, review: MarketplaceReview):
        return {
            "id": review.id,
            "entity": "reviews",
            "chef_id": str(review.chef.supabase_user_id) if review.chef else review.chef_ref_id,
            "chef_ref_id": review.chef_ref_id,
            "dish_id": review.dish_id,
            "dish_ref_id": review.dish_ref_id,
            "author": review.author,
            "rating": review.rating,
            "comment": review.comment,
            "is_public": review.is_public,
            "version": review.version,
            "created_at": self._iso(review.created_at),
            "updated_at": self._iso(review.updated_at),
            "deleted_at": self._iso(review.deleted_at),
        }

    def _server_id(self, item):
        return str(getattr(item, "id"))

    def _iso(self, value):
        return value.isoformat() if value else None
