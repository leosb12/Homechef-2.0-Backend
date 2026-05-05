from ..repositories.marketplace_repository import MarketplaceRepository


class DishDetailService:
    def __init__(self):
        self.repo = MarketplaceRepository()

    def get_detail(self, dish_id: str):
        return self.repo.get_dish_detail(dish_id)

    def add_to_cart(self, user_id: str, dish_id: str, quantity: int):
        return self.repo.add_to_cart_stub(user_id, dish_id, quantity)
