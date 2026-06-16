import re

from ..repositories.chef_repository import ChefRepository
from .availability_rules import normalize_weekly_schedule


class ChefServices:
    def __init__(self):
        self.repo = ChefRepository()

    # CU-12
    def get_profile(self, chef_id: str):
        return self.repo.get_profile(chef_id)

    def save_profile(self, chef_id: str, payload: dict):
        # Permitir guardar cambios generales aunque no se editen especialidades.
        # Si specialties no viene o viene vacio, se conserva el valor existente.
        current_profile = self.repo.get_profile(chef_id)
        incoming_specialties = payload.get("specialties")
        if incoming_specialties is None:
            payload["specialties"] = current_profile.get("specialties", [])
        elif isinstance(incoming_specialties, list):
            cleaned = [str(x).strip() for x in incoming_specialties if str(x).strip()]
            payload["specialties"] = cleaned or current_profile.get("specialties", [])
        else:
            raw = str(incoming_specialties).strip()
            payload["specialties"] = [raw] if raw else current_profile.get("specialties", [])
        identity_updates = {}
        for key in ("first_name", "last_name", "phone"):
            if key in payload:
                identity_updates[key] = str(payload.get(key, "")).strip()
        if identity_updates:
            self.repo.update_user_identity(chef_id, identity_updates)
        profile = self.repo.save_profile(chef_id, payload)
        self.repo.log("chef_profile_updated", {"chef_id": chef_id})
        return profile

    def save_location(self, chef_id: str, payload: dict):
        lat = payload.get("latitude")
        lng = payload.get("longitude")
        if lat is None or lng is None:
            raise ValueError("Ubicacion invalida.")
        lat = float(lat)
        lng = float(lng)
        if lat < -90 or lat > 90:
            raise ValueError("Latitud invalida.")
        if lng < -180 or lng > 180:
            raise ValueError("Longitud invalida.")
        profile = self.repo.save_location(
            chef_id,
            {
                "latitude": lat,
                "longitude": lng,
                "address": str(payload.get("address", "")).strip(),
            },
        )
        self.repo.log("chef_location_updated", {"chef_id": chef_id})
        return profile.get("location", {})

    # CU-13
    def get_availability(self, chef_id: str):
        return self.repo.get_availability(chef_id)

    def save_availability(self, chef_id: str, payload: dict):
        accept_delivery = bool(payload.get("accept_delivery", False))
        accept_pickup = bool(payload.get("accept_pickup", False))
        is_active = bool(payload.get("is_active", True))
        if not accept_delivery and not accept_pickup:
            raise ValueError("Debes habilitar al menos una modalidad: delivery o retiro.")
        try:
            simultaneous_orders_limit = int(payload.get("simultaneous_orders_limit", 0))
        except (TypeError, ValueError):
            simultaneous_orders_limit = 0
        if simultaneous_orders_limit <= 0:
            raise ValueError("Límite de pedidos simultáneos inválido.")

        weekly_schedule = normalize_weekly_schedule(payload.get("weekly_schedule", []))
        if is_active and not weekly_schedule:
            raise ValueError("Debes configurar al menos un horario de atención activo.")
        if is_active:
            for item in weekly_schedule:
                if "delivery" in item["modes"] and not accept_delivery:
                    raise ValueError("Hay horarios con delivery, pero la modalidad delivery está desactivada.")
                if "pickup" in item["modes"] and not accept_pickup:
                    raise ValueError("Hay horarios con retiro, pero la modalidad retiro está desactivada.")

        if not is_active and self.repo.has_active_orders(chef_id):
            raise ValueError("No puedes pausar la atención mientras tienes pedidos activos.")

        data = self.repo.save_availability(
            chef_id,
            {
                **payload,
                "is_active": is_active,
                "weekly_schedule": weekly_schedule,
                "accept_delivery": accept_delivery,
                "accept_pickup": accept_pickup,
                "simultaneous_orders_limit": simultaneous_orders_limit,
            },
        )
        self.repo.log("chef_availability_updated", {"chef_id": chef_id})
        return data

    # CU-14
    def dashboard(self, chef_id: str):
        base = self.repo.dashboard_metrics(chef_id)
        profile = self.repo.get_profile(chef_id)
        availability = self.repo.get_availability(chef_id)
        base["profile_completed"] = bool(profile)
        base["availability_active"] = availability.get("is_active", False)
        base["ai_enabled"] = profile.get("ai_subscription_active", False)
        return base

    # CU-15
    def list_dishes(self, chef_id: str):
        return self.repo.list_dishes(chef_id)

    def save_dish(self, chef_id: str, payload: dict):
        if not str(payload.get("name", "")).strip():
            raise ValueError("Nombre del plato es obligatorio.")
        if not str(payload.get("description", "")).strip():
            raise ValueError("Descripcion del plato es obligatoria.")
        if not str(payload.get("schedule", "")).strip():
            raise ValueError("Horario disponible del plato es obligatorio.")
        try:
            price = float(payload.get("price", 0))
        except (TypeError, ValueError):
            price = 0
        try:
            portions = int(payload.get("portions", 0))
        except (TypeError, ValueError):
            portions = 0
        if price <= 0:
            raise ValueError("Precio invalido.")
        if portions <= 0:
            raise ValueError("Porciones invalidas.")
        payload["ingredients"] = self._clean_ingredients(payload.get("ingredients", []))
        payload["tags"] = self._clean_list(payload.get("tags", []))
        payload["allergens"] = self._clean_list(payload.get("allergens", []))
        if not payload["ingredients"]:
            raise ValueError("Debe seleccionar al menos un ingrediente.")
        if not payload["tags"]:
            raise ValueError("Debe seleccionar al menos una etiqueta.")
        image_url = str(payload.get("image_url", "")).strip()
        if image_url and not (
            image_url.startswith("http://")
            or image_url.startswith("https://")
            or image_url.startswith("/")
        ):
            raise ValueError("Imagen del plato invalida.")
        dish = self.repo.save_dish(chef_id, payload)
        self.repo.log("chef_dish_saved", {"chef_id": chef_id, "dish_id": dish["_id"], "status": dish.get("status")})
        return dish

    def update_dish_status(self, chef_id: str, dish_id: str, status: str):
        if status not in {"published", "paused", "draft", "sold_out"}:
            raise ValueError("Estado de publicacion invalido.")
        dish = self.repo.update_dish_status(chef_id, dish_id, status)
        if not dish:
            raise ValueError("Plato no encontrado.")
        self.repo.log("chef_dish_status_updated", {"chef_id": chef_id, "dish_id": dish_id, "status": status})
        return dish

    def delete_dish(self, chef_id: str, dish_id: str):
        self.repo.delete_dish(chef_id, dish_id)
        self.repo.log("chef_dish_deleted", {"chef_id": chef_id, "dish_id": dish_id})

    def _clean_list(self, raw_value):
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]

    def _clean_ingredients(self, raw_value):
        if not isinstance(raw_value, list):
            return []
        cleaned = []
        seen_names = set()
        for item in raw_value:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                if name:
                    if name.lower() in seen_names:
                        raise ValueError(f"El ingrediente '{name}' está duplicado en la receta.")
                    seen_names.add(name.lower())
                    cleaned.append({
                        "name": name,
                        "quantity": float(item.get("quantity", 1)),
                        "unit": str(item.get("unit", "u")).strip()
                    })
            elif isinstance(item, str):
                if item.strip():
                    name = item.strip()
                    if name.lower() in seen_names:
                        raise ValueError(f"El ingrediente '{name}' está duplicado en la receta.")
                    seen_names.add(name.lower())
                    cleaned.append({
                        "name": name,
                        "quantity": 1,
                        "unit": "u"
                    })
        return cleaned

    # CU-16
    def get_daily_menu(self, chef_id: str):
        return self.repo.get_daily_menu(chef_id)

    def save_daily_menu(self, chef_id: str, payload: dict):
        items = payload.get("items", [])
        is_active = bool(payload.get("is_active", False))
        registered_dishes = self.repo.list_dishes(chef_id)
        dish_by_id = {str(dish.get("_id") or dish.get("id")): dish for dish in registered_dishes}
        if is_active and not registered_dishes:
            raise ValueError("Primero debes registrar platos para crear el menu del dia.")
        if is_active and not items:
            raise ValueError("Debes seleccionar al menos un plato.")
        for item in items:
            dish_id = str(item.get("dish_id") or item.get("id") or "")
            dish = dish_by_id.get(dish_id)
            if not dish:
                raise ValueError(f"Plato no encontrado en el menu: {dish_id}")
            if dish.get("status") != "published":
                raise ValueError("Solo puedes agregar platos publicados al menu del dia.")
            try:
                portions = int(item.get("portions", 0))
            except (TypeError, ValueError):
                portions = 0
            if portions <= 0:
                raise ValueError("Cantidad no valida en el menu del dia.")
            if portions > int(dish.get("portions") or 0):
                raise ValueError("La cantidad supera las porciones disponibles del plato.")
            if str(item.get("status", "available")) not in {"available", "paused", "unavailable", "sold_out"}:
                raise ValueError("Estado invalido en el menu del dia.")
        schedule = str(payload.get("schedule", "")).strip()
        if is_active and not schedule:
            raise ValueError("Debes definir horario del menu.")
        if is_active:
            availability = self.repo.get_availability(chef_id)
            if not availability.get("is_active") or not availability.get("weekly_schedule"):
                raise ValueError("Configura disponibilidad del cocinero antes de activar el menu.")
            self._validate_menu_schedule(schedule, availability)
        menu = self.repo.save_daily_menu(chef_id, payload)
        self.repo.log("chef_daily_menu_updated", {"chef_id": chef_id, "items_count": len(items)})
        return menu

    def _validate_menu_schedule(self, schedule: str, availability: dict):
        match = re.search(r"(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", schedule)
        if not match:
            raise ValueError("Horario incompatible. Usa formato HH:MM - HH:MM.")
        start_time, end_time = match.groups()
        start_minutes = self._minutes(start_time)
        end_minutes = self._minutes(end_time)
        if start_minutes is None or end_minutes is None or end_minutes <= start_minutes:
            raise ValueError("Horario incompatible con la disponibilidad del cocinero.")
        for slot in availability.get("weekly_schedule", []):
            slot_start = self._minutes(slot.get("start_time"))
            slot_end = self._minutes(slot.get("end_time"))
            if (
                slot_start is not None
                and slot_end is not None
                and start_minutes >= slot_start
                and end_minutes <= slot_end
            ):
                return
        raise ValueError("Horario incompatible con la disponibilidad del cocinero.")

    def _minutes(self, value):
        try:
            hour, minute = [int(part) for part in str(value).split(":")]
        except (TypeError, ValueError):
            return None
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return hour * 60 + minute
