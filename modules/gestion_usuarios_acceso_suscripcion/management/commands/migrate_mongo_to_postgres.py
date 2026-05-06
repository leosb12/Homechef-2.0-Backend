from datetime import datetime, timezone
from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from modules.gestion_cocinero.models import ChefAvailability, ChefProfile, DailyMenu, DailyMenuItem, Dish
from modules.gestion_usuarios_acceso_suscripcion.models import AuditEvent, UserProfile
from modules.marketplace_platos.models import MarketplaceFavorite, MarketplacePreference, MarketplaceReview


class Command(BaseCommand):
    help = "Migra datos legacy desde MongoDB hacia PostgreSQL/Supabase de forma idempotente."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Analiza y loguea sin escribir en PostgreSQL.")
        parser.add_argument("--limit", type=int, default=0, help="Limita documentos por coleccion para pruebas.")

    def handle(self, *args, **options):
        uri = settings.MONGODB_URI
        if not uri:
            raise CommandError("MONGODB_URI no esta configurado. Agregalo solo para ejecutar esta migracion.")

        try:
            from pymongo import MongoClient
        except ImportError as exc:
            raise CommandError("pymongo no esta instalado. Ejecuta pip install -r requirements.txt.") from exc

        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        try:
            client.admin.command("ping")
        except Exception as exc:
            raise CommandError(f"No se pudo conectar a MongoDB: {exc}") from exc

        self.db = client[settings.MONGODB_DB]
        self.dry_run = options["dry_run"]
        self.limit = options["limit"] or None
        self.stats = {"ok": 0, "failed": 0, "skipped": 0}

        self.stdout.write(self.style.WARNING("Dry run activo: no se escribira en PostgreSQL." if self.dry_run else "Migracion real iniciada."))

        self._migrate_users()
        self._migrate_user_profiles()
        self._migrate_chef_profiles()
        self._migrate_availability()
        self._migrate_dishes()
        self._migrate_daily_menus()
        self._migrate_marketplace()
        self._migrate_audit()

        self.stdout.write(self.style.SUCCESS(f"Finalizado: {self.stats}"))

    def _docs(self, collection_name):
        cursor = self.db[collection_name].find({})
        if self.limit:
            cursor = cursor.limit(self.limit)
        return cursor

    def _migrate_users(self):
        for doc in self._docs("auth_users"):
            self._run("auth_users", doc, self._save_user)

    def _save_user(self, doc):
        user_id = _uuid_or_none(doc.get("_id"))
        if not user_id:
            return self._skip("auth_users", doc, "auth_users._id no es UUID; crea usuarios en Supabase Auth y mapea manualmente.")

        defaults = {
            "email": str(doc.get("email", "")).lower().strip(),
            "first_name": str(doc.get("first_name", "")).strip(),
            "last_name": str(doc.get("last_name", "")).strip(),
            "full_name": f"{doc.get('first_name', '')} {doc.get('last_name', '')}".strip(),
            "role": str(doc.get("role", "CLIENTE")).upper(),
            "phone": str(doc.get("phone", "")).strip(),
            "is_active": bool(doc.get("is_active", True)),
            "legacy_mongo_id": str(doc.get("_id", "")),
        }
        if self.dry_run:
            return self._ok("auth_users", doc)
        UserProfile.objects.update_or_create(supabase_user_id=user_id, defaults=defaults)
        return self._ok("auth_users", doc)

    def _migrate_user_profiles(self):
        for doc in self._docs("users_profile"):
            self._run("users_profile", doc, self._save_user_profile)

    def _save_user_profile(self, doc):
        profile = _profile_by_mongo_user_id(doc.get("user_id"))
        if not profile:
            return self._skip("users_profile", doc, "usuario destino no encontrado")
        if not self.dry_run:
            for field in ("phone", "address", "accept_terms", "notify_gmail", "notify_push"):
                if field in doc:
                    setattr(profile, field, doc.get(field))
            profile.legacy_mongo_id = str(doc.get("_id", profile.legacy_mongo_id or ""))
            profile.save()
        return self._ok("users_profile", doc)

    def _migrate_chef_profiles(self):
        for doc in self._docs("chef_profiles"):
            self._run("chef_profiles", doc, self._save_chef_profile)

    def _save_chef_profile(self, doc):
        profile = _profile_by_mongo_user_id(doc.get("user_id") or doc.get("chef_id"))
        if not profile:
            return self._skip("chef_profiles", doc, "usuario cocinero no encontrado")
        location = doc.get("location") or {}
        if isinstance(location, str):
            location = {"address": location}
        defaults = {
            "business_name": str(doc.get("business_name", "")).strip(),
            "public_description": str(doc.get("public_description", "")).strip(),
            "specialties": _list(doc.get("specialties")),
            "location_latitude": location.get("latitude"),
            "location_longitude": location.get("longitude"),
            "location_address": str(location.get("address", "")).strip(),
            "schedule": str(doc.get("schedule", "")).strip(),
            "profile_image_url": str(doc.get("profile_image_url", "")).strip(),
            "status": str(doc.get("status", "pending_validation")).strip(),
            "ai_subscription_active": bool(doc.get("ai_subscription_active", False)),
            "legacy_mongo_id": str(doc.get("_id", "")),
        }
        if not self.dry_run:
            ChefProfile.objects.update_or_create(user=profile, defaults=defaults)
        return self._ok("chef_profiles", doc)

    def _migrate_availability(self):
        for doc in self._docs("chef_availability"):
            self._run("chef_availability", doc, self._save_availability)

    def _save_availability(self, doc):
        profile = _profile_by_mongo_user_id(doc.get("chef_id"))
        if not profile:
            return self._skip("chef_availability", doc, "cocinero no encontrado")
        defaults = {
            "is_active": bool(doc.get("is_active", True)),
            "weekly_schedule": _list(doc.get("weekly_schedule")),
            "pickup_schedule": str(doc.get("pickup_schedule", "")).strip(),
            "accept_delivery": bool(doc.get("accept_delivery", True)),
            "accept_pickup": bool(doc.get("accept_pickup", True)),
            "simultaneous_orders_limit": int(doc.get("simultaneous_orders_limit", 10) or 10),
            "legacy_mongo_id": str(doc.get("_id", "")),
        }
        if not self.dry_run:
            ChefAvailability.objects.update_or_create(chef=profile, defaults=defaults)
        return self._ok("chef_availability", doc)

    def _migrate_dishes(self):
        for doc in self._docs("chef_dishes"):
            self._run("chef_dishes", doc, self._save_dish)

    def _save_dish(self, doc):
        profile = _profile_by_mongo_user_id(doc.get("chef_id"))
        if not profile:
            return self._skip("chef_dishes", doc, "cocinero no encontrado")
        dish_id = str(doc.get("_id"))
        defaults = {
            "chef": profile,
            "name": str(doc.get("name", "")).strip(),
            "description": str(doc.get("description", "")).strip(),
            "price": doc.get("price", 0) or 0,
            "portions": int(doc.get("portions", 1) or 1),
            "ingredients": _list(doc.get("ingredients")),
            "tags": _list(doc.get("tags")),
            "allergens": _list(doc.get("allergens")),
            "image_url": str(doc.get("image_url", "")).strip(),
            "schedule": str(doc.get("schedule", "")).strip(),
            "status": str(doc.get("status", "draft")).strip(),
            "legacy_mongo_id": dish_id,
        }
        if not self.dry_run:
            Dish.objects.update_or_create(id=dish_id, defaults=defaults)
        return self._ok("chef_dishes", doc)

    def _migrate_daily_menus(self):
        for doc in self._docs("chef_daily_menu"):
            self._run("chef_daily_menu", doc, self._save_daily_menu)

    @transaction.atomic
    def _save_daily_menu(self, doc):
        profile = _profile_by_mongo_user_id(doc.get("chef_id"))
        if not profile:
            return self._skip("chef_daily_menu", doc, "cocinero no encontrado")
        if self.dry_run:
            return self._ok("chef_daily_menu", doc)
        menu, _ = DailyMenu.objects.update_or_create(
            chef=profile,
            defaults={
                "schedule": str(doc.get("schedule", "")).strip(),
                "is_active": bool(doc.get("is_active", False)),
                "legacy_mongo_id": str(doc.get("_id", "")),
            },
        )
        menu.items.all().delete()
        for index, item in enumerate(doc.get("items", []) or []):
            dish = Dish.objects.filter(id=str(item.get("dish_id")), chef=profile).first()
            if not dish:
                self._skip("chef_daily_menu.items", item, "plato no encontrado")
                continue
            DailyMenuItem.objects.create(
                menu=menu,
                dish=dish,
                portions=int(item.get("portions", 1) or 1),
                status=str(item.get("status", "available")),
                sort_order=index,
            )
        return self._ok("chef_daily_menu", doc)

    def _migrate_marketplace(self):
        for doc in self._docs("marketplace_favorites"):
            self._run("marketplace_favorites", doc, self._save_favorite)
        for doc in self._docs("marketplace_preferences"):
            self._run("marketplace_preferences", doc, self._save_preference)
        for doc in self._docs("marketplace_reviews"):
            self._run("marketplace_reviews", doc, self._save_review)

    def _save_favorite(self, doc):
        profile = _profile_by_mongo_user_id(doc.get("user_id"))
        if not profile:
            return self._skip("marketplace_favorites", doc, "usuario no encontrado")
        if not self.dry_run:
            MarketplaceFavorite.objects.update_or_create(
                user=profile,
                favorite_type=str(doc.get("favorite_type", "")),
                ref_id=str(doc.get("ref_id", "")),
                defaults={"id": str(doc.get("_id"))},
            )
        return self._ok("marketplace_favorites", doc)

    def _save_preference(self, doc):
        profile = _profile_by_mongo_user_id(doc.get("user_id"))
        if not profile:
            return self._skip("marketplace_preferences", doc, "usuario no encontrado")
        if not self.dry_run:
            MarketplacePreference.objects.update_or_create(
                user=profile,
                defaults={
                    "cuisine_types": _list(doc.get("cuisine_types")),
                    "diet_types": _list(doc.get("diet_types")),
                    "price_range": doc.get("price_range") or {},
                    "legacy_mongo_id": str(doc.get("_id", "")),
                },
            )
        return self._ok("marketplace_preferences", doc)

    def _save_review(self, doc):
        chef_ref_id = str(doc.get("chef_id", ""))
        chef = _profile_by_mongo_user_id(chef_ref_id)
        review_id = str(doc.get("_id"))
        if not self.dry_run:
            MarketplaceReview.objects.update_or_create(
                id=review_id,
                defaults={
                    "chef": chef,
                    "chef_ref_id": chef_ref_id,
                    "author": str(doc.get("author", "")),
                    "rating": int(doc.get("rating", 0) or 0),
                    "comment": str(doc.get("comment", "")),
                    "is_public": bool(doc.get("is_public", True)),
                    "legacy_mongo_id": review_id,
                },
            )
        return self._ok("marketplace_reviews", doc)

    def _migrate_audit(self):
        for doc in self._docs("audit_events"):
            self._run("audit_events", doc, self._save_audit)

    def _save_audit(self, doc):
        if not self.dry_run:
            AuditEvent.objects.get_or_create(
                event=str(doc.get("event", "")),
                at=doc.get("at") or datetime.now(timezone.utc),
                defaults={"details": doc.get("details") or {}},
            )
        return self._ok("audit_events", doc)

    def _run(self, collection, doc, handler):
        try:
            handler(doc)
        except Exception as exc:
            self.stats["failed"] += 1
            self.stderr.write(f"[FAIL] {collection} {_doc_id(doc)}: {exc}")

    def _ok(self, collection, doc):
        self.stats["ok"] += 1
        self.stdout.write(f"[OK] {collection} {_doc_id(doc)}")

    def _skip(self, collection, doc, reason):
        self.stats["skipped"] += 1
        self.stdout.write(self.style.WARNING(f"[SKIP] {collection} {_doc_id(doc)}: {reason}"))


def _uuid_or_none(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _profile_by_mongo_user_id(value):
    parsed = _uuid_or_none(value)
    if not parsed:
        return None
    return UserProfile.objects.filter(supabase_user_id=parsed).first()


def _list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]


def _doc_id(doc):
    return str(doc.get("_id", "<sin-id>"))
