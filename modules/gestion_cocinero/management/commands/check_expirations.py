from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from modules.gestion_cocinero.models import InventoryItem
from modules.confianza_administracion_seguridad.services.email_service import EmailService
from modules.confianza_administracion_seguridad.services.notification_service import NotificationService

class Command(BaseCommand):
    help = 'Revisa los inventarios y envía correos si un insumo caduca hoy o ya está caducado.'

    def handle(self, *args, **kwargs):
        today = timezone.localdate()
        
        # Insumos activos que tienen fecha de caducidad
        items = InventoryItem.objects.filter(
            deleted_at__isnull=True, 
            is_active=True,
            expiration_date__isnull=False
        ).select_related('chef')

        alerts_sent = 0

        for item in items:
            days_left = (item.expiration_date - today).days

            # Mandar alerta si caduca hoy (0 días) o si caducó hace exactamente 1 día (-1 días)
            # o si caduca en exactamente 3 días.
            # Nota: para evitar spam, solo alertamos en estos umbrales exactos.
            if days_left in (3, 0, -1):
                EmailService.send_expiration_alert(
                    chef_email=item.chef.email,
                    chef_name=item.chef.first_name or "Cocinero",
                    item_name=item.name,
                    expiration_date=item.expiration_date.isoformat(),
                    days_left=days_left
                )
                try:
                    NotificationService().notify_expiration(chef_user=item.chef, item=item, days_left=days_left)
                except Exception as e:
                    print(f"Error sending in-app expiration notification: {e}")
                alerts_sent += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Alerta enviada a {item.chef.email} por {item.name} (faltan {days_left} días)")
                )

        self.stdout.write(self.style.SUCCESS(f'Revisión completa. Se enviaron {alerts_sent} alertas de vencimiento.'))
