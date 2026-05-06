from ..repositories.marketplace_repository import MarketplaceRepository

ALLOWED_TYPES = {"dish", "chef"}
ALLOWED_CUISINES = {
    "tradicional",
    "fusion",
    "internacional",
    "veg",
    "italiana",
    "mexicana",
    "asiatica",
    "mediterranea",
    "japonesa",
    "vegetariana",
    "arabe",
    "tailandesa",
    "otra",
}
ALLOWED_DIETS = {"regular", "vegetariano", "vegano", "sin_gluten"}


class FavoritesPreferencesService:
    def __init__(self):
        self.repo = MarketplaceRepository()

    def list_favorites(self, user_id: str):
        return self.repo.list_favorites(user_id)

    def add_favorite(self, user_id: str, favorite_type: str, ref_id: str):
        if favorite_type not in ALLOWED_TYPES:
            raise ValueError("Tipo de favorito invalido.")
        ref_id = str(ref_id or "").strip()
        if not ref_id:
            raise ValueError("Debes seleccionar un elemento para favoritos.")
        if not self.repo.favorite_target_exists(favorite_type, ref_id):
            raise ValueError("El elemento seleccionado no esta disponible para favoritos.")
        result = self.repo.add_favorite(user_id, favorite_type, ref_id)
        return result

    def remove_favorite(self, user_id: str, favorite_type: str, ref_id: str):
        if favorite_type not in ALLOWED_TYPES:
            raise ValueError("Tipo de favorito invalido.")
        self.repo.remove_favorite(user_id, favorite_type, ref_id)

    def get_preferences(self, user_id: str):
        return self.repo.get_preferences(user_id)

    def save_preferences(self, user_id: str, payload: dict):
        cuisines = set(payload.get("cuisine_types", []))
        diets = set(payload.get("diet_types", []))
        if not cuisines.issubset(ALLOWED_CUISINES):
            raise ValueError("Preferencia de tipo de cocina invalida.")
        if not diets.issubset(ALLOWED_DIETS):
            raise ValueError("Preferencia de dieta invalida.")
        return self.repo.save_preferences(user_id, payload)
