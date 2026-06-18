from ..repositories.marketplace_repository import MarketplaceRepository


class ReputationService:
    def __init__(self):
        self.repo = MarketplaceRepository()

    def get_reputation(self, chef_id: str):
        return self.repo.get_chef_reputation(chef_id)

    def get_public_profile(self, chef_id: str):
        return self.repo.get_chef_public_profile(chef_id)

    def create_review(self, user_id: str, chef_id: str, payload: dict):
        return self.repo.create_chef_review(user_id, chef_id, payload)

    def create_dish_review(self, user_id: str, dish_id: str, payload: dict):
        return self.repo.create_dish_review(user_id, dish_id, payload)

    def update_review(self, user_id: str, review_id: str, payload: dict):
        return self.repo.update_review(user_id, review_id, payload)

    def delete_review(self, user_id: str, review_id: str):
        return self.repo.delete_review(user_id, review_id)
