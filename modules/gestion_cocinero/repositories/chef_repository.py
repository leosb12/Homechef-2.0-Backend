from datetime import datetime, timezone
from uuid import uuid4

from shared.database.mongo_client import get_collection
from bson import ObjectId


class ChefRepository:
    def __init__(self):
        self.profiles = get_collection("chef_profiles")
        self.users = get_collection("auth_users")
        self.availability = get_collection("chef_availability")
        self.dishes = get_collection("chef_dishes")
        self.daily_menu = get_collection("chef_daily_menu")
        self.audit = get_collection("audit_events")

    def log(self, event: str, details: dict):
        self.audit.insert_one({"event": event, "details": details, "at": datetime.now(timezone.utc)})

    def _serialize_mongo(self, value):
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, dict):
            return {k: self._serialize_mongo(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._serialize_mongo(v) for v in value]
        return value

    # CU-12
    def get_profile(self, chef_id: str):
        profile = self.profiles.find_one({"user_id": chef_id}) or self.profiles.find_one({"chef_id": chef_id}) or {}
        user = self.users.find_one({"_id": chef_id}) or {}

        location = profile.get("location") or {}
        if isinstance(location, str):
            location = {"text": location}

        normalized = {
            "user_id": chef_id,
            "first_name": user.get("first_name", ""),
            "last_name": user.get("last_name", ""),
            "email": user.get("email", ""),
            "phone": user.get("phone", ""),
            "business_name": profile.get("business_name", ""),
            "public_description": profile.get("public_description", ""),
            "specialties": profile.get("specialties", []),
            "location": {
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "address": location.get("address", ""),
            },
            "schedule": profile.get("schedule", ""),
            "profile_image_url": profile.get("profile_image_url", ""),
            "status": profile.get("status", "pending_validation"),
            "updated_at": profile.get("updated_at"),
        }
        return normalized

    def save_profile(self, chef_id: str, payload: dict):
        current = self.profiles.find_one({"user_id": chef_id}) or {}
        current_location = current.get("location") or {}
        doc = {
            **payload,
            "location": payload.get("location", current_location),
            "user_id": chef_id,
            "chef_id": chef_id,
            "updated_at": datetime.now(timezone.utc),
        }
        self.profiles.update_one({"user_id": chef_id}, {"$set": doc}, upsert=True)
        return self.get_profile(chef_id)

    def save_location(self, chef_id: str, payload: dict):
        location = {
            "latitude": payload.get("latitude"),
            "longitude": payload.get("longitude"),
            "address": payload.get("address", ""),
        }
        self.profiles.update_one(
            {"user_id": chef_id},
            {
                "$set": {
                    "location": location,
                    "user_id": chef_id,
                    "chef_id": chef_id,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        return self.get_profile(chef_id)

    def update_user_identity(self, chef_id: str, payload: dict):
        update = {**payload, "updated_at": datetime.now(timezone.utc)}
        self.users.update_one({"_id": chef_id}, {"$set": update})

    # CU-13
    def get_availability(self, chef_id: str):
        return self.availability.find_one({"chef_id": chef_id}) or {
            "chef_id": chef_id,
            "is_active": True,
            "weekly_schedule": [],
            "pickup_schedule": "",
            "accept_delivery": True,
            "accept_pickup": True,
            "simultaneous_orders_limit": 10,
        }

    def save_availability(self, chef_id: str, payload: dict):
        doc = {**payload, "chef_id": chef_id, "updated_at": datetime.now(timezone.utc)}
        self.availability.update_one({"chef_id": chef_id}, {"$set": doc}, upsert=True)
        return self.get_availability(chef_id)

    # CU-15
    def list_dishes(self, chef_id: str):
        raw = list(self.dishes.find({"chef_id": chef_id}).sort([("updated_at", -1)]))
        return self._serialize_mongo(raw)

    def get_dish(self, chef_id: str, dish_id: str):
        dish = self.dishes.find_one({"_id": dish_id, "chef_id": chef_id})
        return self._serialize_mongo(dish) if dish else None

    def save_dish(self, chef_id: str, payload: dict):
        dish_id = payload.get("_id") or str(uuid4())
        action = payload.get("action", "draft")
        status = "published" if action == "publish" else payload.get("status", "draft")
        doc = {
            "_id": dish_id,
            "chef_id": chef_id,
            "name": payload["name"],
            "description": payload.get("description", ""),
            "price": float(payload["price"]),
            "portions": int(payload["portions"]),
            "ingredients": payload.get("ingredients", []),
            "tags": payload.get("tags", []),
            "allergens": payload.get("allergens", []),
            "image_url": payload.get("image_url", ""),
            "schedule": payload.get("schedule", ""),
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }
        self.dishes.update_one({"_id": dish_id, "chef_id": chef_id}, {"$set": doc}, upsert=True)
        return self.get_dish(chef_id, dish_id)

    def update_dish_status(self, chef_id: str, dish_id: str, status: str):
        self.dishes.update_one(
            {"_id": dish_id, "chef_id": chef_id},
            {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}},
        )
        return self.get_dish(chef_id, dish_id)

    def delete_dish(self, chef_id: str, dish_id: str):
        self.dishes.delete_one({"_id": dish_id, "chef_id": chef_id})

    # CU-16
    def get_daily_menu(self, chef_id: str):
        menu = self.daily_menu.find_one({"chef_id": chef_id}) or {"chef_id": chef_id, "items": [], "is_active": False}
        return self._serialize_mongo(menu)

    def save_daily_menu(self, chef_id: str, payload: dict):
        doc = {**payload, "chef_id": chef_id, "updated_at": datetime.now(timezone.utc)}
        self.daily_menu.update_one({"chef_id": chef_id}, {"$set": doc}, upsert=True)
        return self.get_daily_menu(chef_id)

    # CU-14
    def dashboard_metrics(self, chef_id: str):
        dishes = list(self.dishes.find({"chef_id": chef_id}))
        published = [d for d in dishes if d.get("status") == "published"]
        return {
            "sales_total": 0,
            "income_total": 0,
            "commissions_total": 0,
            "orders_recent": [],
            "dishes_total": len(dishes),
            "dishes_published": len(published),
            "kitchen_status": "active",
            "suggestions": ["Publica tu menu del dia", "Actualiza porciones para platos destacados"],
        }
