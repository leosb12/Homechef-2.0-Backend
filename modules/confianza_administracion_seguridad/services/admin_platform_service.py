import logging
from django.utils import timezone
from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.gestion_cocinero.models import ChefProfile
from modules.marketplace_platos.models import Dish
from modules.confianza_administracion_seguridad.services.notification_service import NotificationService
from modules.confianza_administracion_seguridad.services.email_service import EmailService

logger = logging.getLogger(__name__)

class AdminPlatformError(ValueError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code

class AdminPlatformService:
    def list_users(self):
        users = UserProfile.objects.all().order_by("-created_at")
        result = []
        for u in users:
            result.append({
                "id": str(u.id),
                "email": u.email,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            })
        return result

    def toggle_user_block(self, user_id: str):
        user = UserProfile.objects.filter(id=user_id).first()
        if not user:
            raise AdminPlatformError("Usuario no encontrado", "user_not_found")
        
        # Toggle is_active
        user.is_active = not user.is_active
        user.save(update_fields=["is_active", "updated_at"])
        
        # Notificar
        try:
            NotificationService().notify_user_blocked(user, not user.is_active)
            action_text = "bloqueada" if not user.is_active else "desbloqueada"
            subject = "Actualización de estado de cuenta"
            title = f"Cuenta {action_text.capitalize()}"
            message = f"Te informamos que tu cuenta en HomeChef ha sido {action_text} por nuestro equipo administrativo."
            color = "#ef4444" if not user.is_active else "#22c55e"
            EmailService.send_admin_action_alert(user.email, user.first_name, subject, title, message, color)
        except Exception as e:
            logger.error(f"Error notificando bloqueo/desbloqueo a {user.email}: {e}")
            
        return {
            "id": str(user.id),
            "is_active": user.is_active
        }

    def list_pending_chefs(self):
        chefs = ChefProfile.objects.filter(status="pending_validation").select_related("user").order_by("-created_at")
        result = []
        for c in chefs:
            result.append({
                "id": str(c.id),
                "user_id": str(c.user.id),
                "business_name": c.business_name,
                "first_name": c.user.first_name,
                "last_name": c.user.last_name,
                "email": c.user.email,
                "specialties": c.specialties,
                "city": getattr(c, 'city', None),
                "address": getattr(c, 'location_address', None),
                "profile_picture": getattr(c, 'profile_image_url', None),
                "kitchen_photos": getattr(c, 'kitchen_photos', []),
                "created_at": c.created_at.isoformat() if hasattr(c, 'created_at') and c.created_at else None,
            })
        return result

    def validate_chef(self, chef_id: str, action: str):
        chef = ChefProfile.objects.filter(id=chef_id).select_related("user").first()
        if not chef:
            raise AdminPlatformError("Perfil de cocinero no encontrado", "chef_not_found")
        
        if action not in ["approved", "rejected"]:
            raise AdminPlatformError("Acción inválida", "invalid_action")
            
        chef.status = action
        chef.save(update_fields=["status", "updated_at"])
        
        # Notificar
        try:
            NotificationService().notify_chef_validation(chef.user, action)
            if action == "approved":
                subject = "¡Felicidades! Perfil de Cocinero Aprobado"
                title = "Perfil Aprobado 🎉"
                message = "Tu solicitud ha sido aprobada. Ya puedes comenzar a publicar platos en HomeChef."
                color = "#22c55e"
            else:
                subject = "Actualización sobre tu solicitud de Cocinero"
                title = "Solicitud Rechazada"
                message = "Lamentamos informarte que tu solicitud para ser cocinero no ha sido aprobada en esta ocasión."
                color = "#ef4444"
                
            EmailService.send_admin_action_alert(chef.user.email, chef.user.first_name, subject, title, message, color)
        except Exception as e:
            logger.error(f"Error notificando validación de cocinero a {chef.user.email}: {e}")
            
        return {
            "id": str(chef.id),
            "status": chef.status
        }

    def list_publications(self):
        dishes = Dish.objects.all().select_related("chef__chef_profile").order_by("-created_at")
        result = []
        for d in dishes:
            chef_profile = getattr(d.chef, 'chef_profile', None)
            business_name = chef_profile.business_name if chef_profile else f"{d.chef.first_name} {d.chef.last_name}"
            result.append({
                "id": str(d.id),
                "name": d.name,
                "price": float(d.price),
                "status": d.status,
                "chef_business_name": business_name,
                "image": d.image_url if getattr(d, 'image_url', None) else None,
                "deleted_at": d.deleted_at.isoformat() if d.deleted_at else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            })
        return result

    def toggle_publication_action(self, dish_id: str, action: str):
        dish = Dish.objects.filter(id=dish_id).select_related("chef").first()
        if not dish:
            raise AdminPlatformError("Plato no encontrado", "dish_not_found")
            
        if action == "pause":
            dish.status = "paused"
            dish.save(update_fields=["status", "updated_at"])
        elif action == "soft_delete":
            dish.deleted_at = timezone.now()
            dish.status = "paused"
            dish.save(update_fields=["deleted_at", "status", "updated_at"])
        elif action == "restore":
            dish.deleted_at = None
            dish.status = "draft"
            dish.save(update_fields=["deleted_at", "status", "updated_at"])
        else:
            raise AdminPlatformError("Acción inválida", "invalid_action")
            
        # Notificar al cocinero (solo al pausar o eliminar)
        if action in ["pause", "soft_delete"]:
            try:
                NotificationService().notify_publication_action(dish.chef, dish.name, "paused" if action == "pause" else "deleted")
                subject = "Aviso sobre tu publicación"
                title = "Acción Administrativa"
                msg_action = "pausado" if action == "pause" else "eliminado"
                message = f"Tu plato '{dish.name}' ha sido {msg_action} por la administración. Por favor revisa que cumpla con las políticas de HomeChef."
                color = "#f59e0b" if action == "pause" else "#ef4444"
                EmailService.send_admin_action_alert(dish.chef.email, dish.chef.first_name, subject, title, message, color)
            except Exception as e:
                logger.error(f"Error notificando acción sobre publicación a {dish.chef.email}: {e}")

        return {
            "id": str(dish.id),
            "status": dish.status,
            "deleted_at": dish.deleted_at.isoformat() if dish.deleted_at else None
        }
