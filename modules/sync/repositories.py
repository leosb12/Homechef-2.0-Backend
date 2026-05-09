from django.db.models import Q

from modules.gestion_cocinero.models import Dish
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.sync.models import SyncOperation


class SyncRepository:
    def find_operation(self, operation_id: str):
        return SyncOperation.objects.filter(operation_id=str(operation_id)).first()

    def record_operation(self, **kwargs):
        return SyncOperation.objects.create(**kwargs)

    def find_user(self, user_id: str):
        return UserProfile.objects.filter(supabase_user_id=user_id).first()

    def get_dish_for_sync(self, dish_id: str, chef: UserProfile):
        return Dish.objects.filter(id=str(dish_id), chef=chef).first()

    def get_changed_dishes(self, chef: UserProfile, last_sync):
        query = Q(chef=chef)
        if last_sync:
            query &= Q(updated_at__gt=last_sync) | Q(deleted_at__gt=last_sync)
        return Dish.objects.filter(query).order_by("updated_at")
