from ..repositories.public_dashboard_repository import PublicDashboardRepository


class PublicDashboardService:
    def __init__(self):
        self.repository = PublicDashboardRepository()

    def get_public_dashboard(self, latitude: str = "", longitude: str = ""):
        dishes = self.repository.fetch_public_dishes(
            latitude=latitude,
            longitude=longitude,
        )
        if not dishes:
            return {
                "status": "empty",
                "message": "No hay platos disponibles por el momento.",
                "dishes": [],
            }
        return {
            "status": "ok",
            "message": "Dashboard publico cargado correctamente.",
            "dishes": dishes,
        }
