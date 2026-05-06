# HomeChef Backend

Monolito modular Django + DRF. La persistencia principal ahora es PostgreSQL en Supabase, con Supabase Auth como identidad principal de usuarios finales y Supabase Storage para uploads.

## Arquitectura

- React Vite y Flutter autentican con Supabase Auth.
- Los clientes envian `Authorization: Bearer <supabase_access_token>` a Django.
- Django valida el token contra Supabase Auth, sincroniza/crea `UserProfile` local y aplica permisos/roles.
- Django no maneja passwords de usuarios finales.
- Los archivos se suben a Supabase Storage desde Django y PostgreSQL guarda solo metadata.

## Instalacion

```bash
python -m venv ..\.venv
..\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Completa `.env` con:

- `DB_PASSWORD`: password real de PostgreSQL Supabase.
- `SUPABASE_ANON_KEY`: publica, valida Auth.
- `SUPABASE_SERVICE_ROLE_KEY`: solo backend, usada para Storage seguro.

## Base de datos

Por defecto se usa el host directo:

```env
DB_HOST=db.pimmweiqnensrevyzvqn.supabase.co
DB_PORT=5432
```

Si tu entorno no soporta IPv6 o el host directo no resuelve, usa el Session Pooler de Supabase en `DB_HOST`/`DB_PORT` y conserva `sslmode=require`.

## Migraciones

```bash
python manage.py migrate
```

## Ejecutar

```bash
python manage.py runserver 0.0.0.0:8000
```

## Migrar datos desde MongoDB

La app ya no usa MongoDB en runtime. Para migrar datos legacy, configura temporalmente:

```env
MONGODB_URI=
MONGODB_DB=homechef
```

Primero prueba:

```bash
python manage.py migrate_mongo_to_postgres --dry-run
```

Luego ejecuta:

```bash
python manage.py migrate_mongo_to_postgres
```

El comando es idempotente con `update_or_create`, loguea OK/SKIP/FAIL y no migra passwords de Mongo. Los usuarios deben existir o poder mapearse por UUID de Supabase.

## Storage

Endpoint protegido:

```http
POST /api/v1/uploads/
Authorization: Bearer <supabase_access_token>
Content-Type: multipart/form-data
```

Campos:

- `file`: archivo.
- `type`: categoria opcional.

Ruta en bucket:

```text
users/<supabase_user_id>/<type>/<uuid>_<filename>
```

## Mapping Mongo a PostgreSQL

- `auth_users` + `users_profile` -> `user_profiles`
- `chef_profiles` -> `chef_profiles`
- `chef_availability` -> `chef_availability`
- `chef_dishes` -> `chef_dishes`
- `chef_daily_menu.items[]` -> `chef_daily_menu` + `chef_daily_menu_items`
- `marketplace_favorites` -> `marketplace_favorites`
- `marketplace_preferences` -> `marketplace_preferences`
- `marketplace_reviews` -> `marketplace_reviews`
- `audit_events` -> `audit_events`
- uploads nuevos -> `uploaded_files` + objetos en Supabase Storage

## Deploy

- No subas `.env`.
- No expongas `SUPABASE_SERVICE_ROLE_KEY`.
- En frontend publica solo `VITE_SUPABASE_URL` y `VITE_SUPABASE_ANON_KEY`.
- Ejecuta `python manage.py migrate` durante deploy.
- Configura CORS con los dominios reales del frontend.
- Crea el bucket `uploads` en Supabase Storage antes de usar uploads.
