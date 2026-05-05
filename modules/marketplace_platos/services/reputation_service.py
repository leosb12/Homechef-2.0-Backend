from ..repositories.marketplace_repository import MarketplaceRepository


class ReputationService:
    def __init__(self):
        self.repo = MarketplaceRepository()

    def get_reputation(self, chef_id: str):
        return self.repo.get_chef_reputation(chef_id)
