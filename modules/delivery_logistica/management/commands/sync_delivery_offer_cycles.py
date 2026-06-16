from django.core.management.base import BaseCommand

from modules.delivery_logistica.services.delivery_offer_service import DeliveryOfferService


class Command(BaseCommand):
    help = "Sincroniza ciclos de oferta delivery, expiraciones y tablero abierto."

    def handle(self, *args, **options):
        service = DeliveryOfferService()
        service.sync_open_assignments()
        self.stdout.write(self.style.SUCCESS("Sincronizacion de ofertas delivery completada."))
