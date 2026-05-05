from shared.database.mongo_client import get_collection


class PublicDashboardRepository:
    """Repositorio MongoDB para CU-07/CU-08 conectado al menu activo del cocinero."""

    def __init__(self):
        self.dishes = get_collection("chef_dishes")
        self.daily_menu = get_collection("chef_daily_menu")
        self.chef_profiles = get_collection("chef_profiles")
        self.users = get_collection("auth_users")

    def fetch_public_dishes(self):
        docs = self._build_marketplace_docs()
        docs.sort(key=lambda d: (0 if d["is_featured"] else 1, d["name"].lower()))
        return docs

    def fetch_client_explore(
        self,
        query: str = "",
        featured: str = "",
        sort: str = "",
        min_price: str = "",
        max_price: str = "",
        availability: str = "",
        cuisine_type: str = "",
        diet_type: str = "",
    ):
        docs = self._build_marketplace_docs()
        q = str(query or "").strip().lower()

        filtered = []
        for doc in docs:
            if q and q not in doc["name"].lower():
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
            if min_price and doc["approx_price"] < float(min_price):
                continue
            if max_price and doc["approx_price"] > float(max_price):
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
        else:
            filtered.sort(key=lambda d: (0 if d["is_featured"] else 1, d["name"].lower()))

        return filtered

    def _build_marketplace_docs(self):
        active_menus = list(self.daily_menu.find({"is_active": True}))
        if not active_menus:
            return []

        chef_ids = {str(menu.get("chef_id", "")) for menu in active_menus if menu.get("chef_id")}
        profiles = {
            str(profile.get("user_id") or profile.get("chef_id")): profile
            for profile in self.chef_profiles.find({"user_id": {"$in": list(chef_ids)}})
        }
        users = {str(u.get("_id")): u for u in self.users.find({"_id": {"$in": list(chef_ids)}})}

        result = []
        for menu in active_menus:
            chef_id = str(menu.get("chef_id", ""))
            items = menu.get("items", [])
            item_ids = [str(item.get("dish_id", "")) for item in items if item.get("dish_id")]
            if not item_ids:
                continue

            dishes = {
                str(dish.get("_id")): dish
                for dish in self.dishes.find({"_id": {"$in": item_ids}, "status": {"$in": ["published", "draft"]}})
            }
            profile = profiles.get(chef_id, {})
            user = users.get(chef_id, {})
            chef_name = (
                profile.get("business_name")
                or f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                or "Cocinero HomeChef"
            )

            for item in items:
                dish_id = str(item.get("dish_id", ""))
                dish = dishes.get(dish_id)
                if not dish:
                    continue

                menu_status = str(item.get("status", "available"))
                is_available = menu_status == "available" and int(item.get("portions", 0)) > 0

                result.append(
                    {
                        "id": dish_id,
                        "name": dish.get("name", ""),
                        "image_url": dish.get("image_url", ""),
                        "approx_price": float(dish.get("price", 0)),
                        "chef_name": chef_name,
                        "is_featured": dish.get("status") == "published",
                        "is_available": is_available,
                        "distance_km": 0.0,
                        "rating": 0.0,
                        "popularity": 0,
                        "cuisine_type": "tradicional",
                        "diet_type": "regular",
                    }
                )

        return result
