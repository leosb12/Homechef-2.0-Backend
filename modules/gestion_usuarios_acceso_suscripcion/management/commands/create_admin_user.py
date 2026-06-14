import requests

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.gestion_usuarios_acceso_suscripcion.repositories.user_repository import UserRepository


class Command(BaseCommand):
    help = "Crea un usuario administrador en Supabase Auth y sincroniza su perfil local."

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            default="admin@gmail.com",
            help="Correo del administrador. Default: admin@gmail.com",
        )
        parser.add_argument(
            "--password",
            default="Admin.12",
            help="Contrasena del administrador. Default: Admin.12",
        )
        parser.add_argument(
            "--first-name",
            default="Admin",
            help="Nombre del administrador. Default: Admin",
        )
        parser.add_argument(
            "--last-name",
            default="HomeChef",
            help="Apellido del administrador. Default: HomeChef",
        )

    def handle(self, *args, **options):
        service_role = settings.SUPABASE_SERVICE_ROLE_KEY
        supabase_url = settings.SUPABASE_URL.rstrip("/")
        email = options["email"].strip().lower()
        password = options["password"]
        first_name = options["first_name"].strip()
        last_name = options["last_name"].strip()

        if not service_role:
            raise CommandError(
                "SUPABASE_SERVICE_ROLE_KEY no esta configurada en el backend."
            )
        if not supabase_url:
            raise CommandError("SUPABASE_URL no esta configurada en el backend.")
        if len(password) < 8:
            raise CommandError("La contrasena debe tener al menos 8 caracteres.")

        existing = UserProfile.objects.filter(email=email).first()
        if existing:
            existing.role = UserProfile.ROLE_ADMIN
            existing.is_active = True
            existing.first_name = first_name or existing.first_name
            existing.last_name = last_name or existing.last_name
            existing.full_name = f"{existing.first_name} {existing.last_name}".strip()
            existing.accept_terms = True
            existing.save(
                update_fields=[
                    "role",
                    "is_active",
                    "first_name",
                    "last_name",
                    "full_name",
                    "accept_terms",
                    "updated_at",
                ]
            )
            self.stdout.write(
                self.style.WARNING(
                    f"El usuario local {email} ya existia. Se actualizo a ADMINISTRADOR."
                )
            )
            return

        response = requests.post(
            f"{supabase_url}/auth/v1/admin/users",
            headers={
                "apikey": service_role,
                "Authorization": f"Bearer {service_role}",
                "Content-Type": "application/json",
            },
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "first_name": first_name,
                    "last_name": last_name,
                    "full_name": f"{first_name} {last_name}".strip(),
                    "role": UserProfile.ROLE_ADMIN,
                    "accept_terms": True,
                },
                "app_metadata": {
                    "role": UserProfile.ROLE_ADMIN,
                },
            },
            timeout=20,
        )

        if response.status_code >= 400:
            message = response.text
            raise CommandError(
                f"No se pudo crear el admin en Supabase Auth ({response.status_code}): {message}"
            )

        data = response.json()
        supabase_user_id = data.get("id")
        if not supabase_user_id:
            raise CommandError("Supabase no devolvio el id del usuario creado.")

        UserRepository().create_or_update_profile(
            supabase_user_id,
            {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "full_name": f"{first_name} {last_name}".strip(),
                "role": UserProfile.ROLE_ADMIN,
                "accept_terms": True,
                "phone": "",
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Administrador creado correctamente: {email} ({supabase_user_id})"
            )
        )
