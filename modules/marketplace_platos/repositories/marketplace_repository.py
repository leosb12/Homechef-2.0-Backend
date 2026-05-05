from datetime import datetime, timezone
from uuid import uuid4

from shared.database.mongo_client import get_collection


class MarketplaceRepository:
    def __init__(self):
        self.dishes = get_collection("chef_dishes")
        self.daily_menu = get_collection("chef_daily_menu")
        self.chef_profiles = get_collection("chef_profiles")
        self.users = get_collection("auth_users")
        self.favorites = get_collection("marketplace_favorites")
        self.preferences = get_collection("marketplace_preferences")
        self.reviews = get_collection("marketplace_reviews")
        self._seed_reviews()

    def get_dish_detail(self, dish_id: str):
        dish = self.dishes.find_one({"_id": dish_id})
        if not dish:
            return None

        chef_id = str(dish.get("chef_id", ""))
        menu = self.daily_menu.find_one({"chef_id": chef_id, "is_active": True}) or {}
        menu_item = None
        for item in menu.get("items", []):
            if str(item.get("dish_id", "")) == dish_id:
                menu_item = item
                break

        if not menu_item:
            return None

        portions = int(menu_item.get("portions", dish.get("portions", 0)) or 0)
        menu_status = str(menu_item.get("status", "available"))
        is_available = menu_status == "available" and portions > 0 and dish.get("status") in {"published", "draft"}

        profile = self.chef_profiles.find_one({"user_id": chef_id}) or self.chef_profiles.find_one({"chef_id": chef_id}) or {}
        user = self.users.find_one({"_id": chef_id}) or {}
        chef_name = (
            profile.get("business_name")
            or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
            or "Cocinero HomeChef"
        )

        return {
            "id": str(dish.get("_id", "")),
            "name": dish.get("name", ""),
            "description": dish.get("description", "Plato casero preparado con ingredientes frescos."),
            "image_url": dish.get("image_url", ""),
            "gallery": [dish.get("image_url", "")],
            "approx_price": float(dish.get("price", 0)),
            "ingredients": dish.get("ingredients", ["ingrediente base"]),
            "tags": dish.get("tags", ["casero"]),
            "allergens": dish.get("allergens", []),
            "available_portions": portions,
            "is_available": is_available,
            "schedule": menu.get("schedule") or dish.get("schedule", "11:00 - 21:00"),
            "delivery_available": True,
            "chef": {
                "id": chef_id,
                "name": chef_name,
                "is_available": True,
            },
            "reputation": self.get_chef_reputation(chef_id),
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
        docs = self.favorites.find({"user_id": user_id})
        return list(docs)

    def add_favorite(self, user_id: str, favorite_type: str, ref_id: str):
        existing = self.favorites.find_one({"user_id": user_id, "favorite_type": favorite_type, "ref_id": ref_id})
        if existing:
            return {"duplicated": True}
        self.favorites.insert_one(
            {
                "_id": str(uuid4()),
                "user_id": user_id,
                "favorite_type": favorite_type,
                "ref_id": ref_id,
                "created_at": datetime.now(timezone.utc),
            }
        )
        return {"duplicated": False}

    def remove_favorite(self, user_id: str, favorite_type: str, ref_id: str):
        self.favorites.delete_one({"user_id": user_id, "favorite_type": favorite_type, "ref_id": ref_id})

    def get_preferences(self, user_id: str):
        return self.preferences.find_one({"user_id": user_id}) or {"user_id": user_id, "cuisine_types": [], "diet_types": [], "price_range": {}}

    def save_preferences(self, user_id: str, payload: dict):
        doc = {**payload, "user_id": user_id, "updated_at": datetime.now(timezone.utc)}
        self.preferences.update_one({"user_id": user_id}, {"$set": doc}, upsert=True)
        return self.get_preferences(user_id)

    def get_chef_reputation(self, chef_id: str):
        reviews = list(self.reviews.find({"chef_id": chef_id, "is_public": True}))
        if not reviews:
            return {
                "rating_avg": 0,
                "reviews_count": 0,
                "trust_level": "Sin reputacion consolidada",
                "reviews": [],
            }
        avg = sum([r.get("rating", 0) for r in reviews]) / len(reviews)
        return {
            "rating_avg": round(avg, 2),
            "reviews_count": len(reviews),
            "trust_level": "Alto" if avg >= 4.5 else "Medio",
            "reviews": [{"author": r.get("author", "Cliente"), "rating": r.get("rating", 0), "comment": r.get("comment", "")} for r in reviews[:5]],
        }

    def _seed_reviews(self):
        if self.reviews.count_documents({}) > 0:
            return
        self.reviews.insert_many(
            [
                {"_id": str(uuid4()), "chef_id": "chef-default", "author": "Ana", "rating": 5, "comment": "Muy rico y puntual.", "is_public": True},
                {"_id": str(uuid4()), "chef_id": "chef-default", "author": "Luis", "rating": 4, "comment": "Buen sabor, porcion correcta.", "is_public": True},
            ]
        )
