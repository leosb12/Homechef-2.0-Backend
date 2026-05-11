from ..repositories.public_dashboard_repository import PublicDashboardRepository


class ClientExploreService:
    def __init__(self):
        self.repository = PublicDashboardRepository()

    def get_explore_dashboard(
        self,
        query: str,
        featured: str,
        sort: str,
        min_price: str,
        max_price: str,
        max_distance_km: str,
        availability: str,
        cuisine_type: str,
        diet_type: str,
        location_available: str,
        latitude: str = "",
        longitude: str = "",
    ):
        dishes = self.repository.fetch_client_explore(
            query=query,
            featured=featured,
            sort=sort,
            min_price=min_price,
            max_price=max_price,
            max_distance_km=max_distance_km,
            availability=availability,
            cuisine_type=cuisine_type,
            diet_type=diet_type,
            latitude=latitude,
            longitude=longitude,
        )
        location_message = ""
        if location_available == "false":
            location_message = " Ubicación no disponible: mostrando platos generales."
        if not dishes:
            return {
                "status": "empty",
                "message": "No se encontraron platos con los filtros aplicados.",
                "dishes": [],
            }
        return {
            "status": "ok",
            "message": f"Exploración de platos cargada correctamente.{location_message}".strip(),
            "dishes": dishes,
        }
