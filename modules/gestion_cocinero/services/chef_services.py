from ..repositories.chef_repository import ChefRepository


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
        self.repo.update_user_identity(
            chef_id,
            {
                "first_name": str(payload.get("first_name", "")).strip(),
                "last_name": str(payload.get("last_name", "")).strip(),
                "phone": str(payload.get("phone", "")).strip(),
            },
        )
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
        if not payload.get("accept_delivery") and not payload.get("accept_pickup"):
            raise ValueError("Debes habilitar al menos una modalidad: delivery o retiro.")
        if payload.get("simultaneous_orders_limit", 0) <= 0:
            raise ValueError("Limite de pedidos simultaneos invalido.")
        data = self.repo.save_availability(chef_id, payload)
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
        if not payload.get("name"):
            raise ValueError("Nombre del plato es obligatorio.")
        if float(payload.get("price", 0)) <= 0:
            raise ValueError("Precio invalido.")
        if int(payload.get("portions", 0)) <= 0:
            raise ValueError("Porciones invalidas.")
        dish = self.repo.save_dish(chef_id, payload)
        self.repo.log("chef_dish_saved", {"chef_id": chef_id, "dish_id": dish["_id"], "status": dish.get("status")})
        return dish

    def update_dish_status(self, chef_id: str, dish_id: str, status: str):
        if status not in {"published", "paused", "draft", "sold_out"}:
            raise ValueError("Estado de publicacion invalido.")
        dish = self.repo.update_dish_status(chef_id, dish_id, status)
        self.repo.log("chef_dish_status_updated", {"chef_id": chef_id, "dish_id": dish_id, "status": status})
        return dish

    def delete_dish(self, chef_id: str, dish_id: str):
        self.repo.delete_dish(chef_id, dish_id)
        self.repo.log("chef_dish_deleted", {"chef_id": chef_id, "dish_id": dish_id})

    # CU-16
    def get_daily_menu(self, chef_id: str):
        return self.repo.get_daily_menu(chef_id)

    def save_daily_menu(self, chef_id: str, payload: dict):
        items = payload.get("items", [])
        if not items:
            raise ValueError("Debes seleccionar al menos un plato.")
        for item in items:
            if int(item.get("portions", 0)) <= 0:
                raise ValueError("Cantidad no valida en el menu del dia.")
        if not payload.get("schedule"):
            raise ValueError("Debes definir horario del menu.")
        menu = self.repo.save_daily_menu(chef_id, payload)
        self.repo.log("chef_daily_menu_updated", {"chef_id": chef_id, "items_count": len(items)})
        return menu
