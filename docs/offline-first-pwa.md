# Soporte offline-first/PWA - Sprint 1

## Objetivo

El sistema implementa una base offline-first para permitir que funcionalidades criticas sigan operando sin internet o con conectividad inestable. En este sprint el backend expone endpoints de sincronizacion idempotentes para que un frontend PWA pueda guardar operaciones localmente y enviarlas cuando recupere conexion.

## Alcance implementado

Este repositorio contiene el backend Django/DRF. No se encontro frontend dentro de la estructura del proyecto, por lo que la base PWA queda definida como contrato tecnico para el cliente: `manifest.json`, service worker, cache de assets, IndexedDB, cola local y detector de conexion deben implementarse en el proyecto frontend consumiendo estos endpoints.

En backend se implemento:

- `POST /sync/` y `POST /api/v1/sync/`
- `GET /sync/?lastSync=...` y `GET /api/v1/sync/?lastSync=...`
- tabla `sync_operations` para idempotencia por `operation_id`
- soporte para `CREATE`, `UPDATE` y `DELETE`
- deteccion simple de conflictos por `version` o `updated_at`
- borrado logico en entidades sincronizables con `deleted_at`

## Entidades sincronizables

Por seguridad existe una lista blanca en `modules/sync/services.py`.

Entidades habilitadas en Sprint 1:

- `dishes`
- `platos`
- `chef_profiles`
- `perfil_cocinero`
- `chef_availability`
- `disponibilidad_cocinero`
- `daily_menus`
- `menus_diarios`
- `favorites`
- `favoritos`
- `preferences`
- `preferencias`
- `reviews`
- `resenas`

Se eligieron estas entidades porque representan datos que el usuario puede preparar, consultar o modificar con conectividad inestable: catalogo de platos, perfil publico del cocinero, disponibilidad, menu diario, preferencias de exploracion, favoritos y resenas. Todas quedan controladas por usuario autenticado y por una lista blanca en `modules/sync/services.py`.

Quedan fuera de esta fase:

- usuarios/autenticacion: dependen de Supabase Auth y requieren validacion externa.
- pagos, metodos de pago y suscripciones: dependen de Stripe, CoinGate, callbacks y consistencia transaccional externa.
- notificaciones en tiempo real: requieren conexion activa.
- pedidos, carrito, ordenes, ventas, reservas y direcciones separadas: no tienen modelos persistentes propios en este backend al momento de esta implementacion. La direccion actual existe como campo de `UserProfile`, pero el perfil de usuario no se habilito para escritura offline porque su creacion y seguridad estan acopladas a Supabase.

## Flujo de sincronizacion

El frontend mantiene una cola local de operaciones en IndexedDB. Cada operacion debe incluir:

```json
{
  "operation_id": "uuid",
  "entity": "dishes",
  "action": "CREATE",
  "local_id": "temp-123",
  "server_id": null,
  "payload": {},
  "version": null,
  "created_at": "2026-05-09T20:00:00Z"
}
```

Cuando vuelve la conexion, el cliente envia:

```http
POST /api/v1/sync/
```

El backend responde con:

- `synced`: operaciones aplicadas correctamente
- `errors`: operaciones rechazadas por validacion o reglas de dominio
- `conflicts`: operaciones no aplicadas porque el servidor tiene una version mas nueva
- `server_time`: fecha del servidor para guardar como nueva marca de sincronizacion

## CREATE offline

1. La PWA crea el plato con un `local_id` temporal.
2. Guarda la operacion `CREATE` en IndexedDB.
3. Al sincronizar, el backend crea el registro real.
4. El backend devuelve `local_id` y `server_id`.
5. El frontend reemplaza referencias locales por el `server_id`.

## UPDATE offline

1. La PWA guarda cambios del registro con su `server_id` y `version` conocida.
2. Al sincronizar, el backend verifica si el servidor fue modificado despues de `last_sync` o si su `version` es mayor.
3. Si no hay conflicto, actualiza el registro e incrementa `version`.
4. Si hay conflicto, devuelve `SERVER_VERSION_NEWER`.

## DELETE offline

1. La PWA guarda una operacion `DELETE` con `server_id`.
2. El backend valida conflicto igual que en UPDATE.
3. Si no hay conflicto, marca `deleted_at` en lugar de borrar fisicamente.
4. El borrado logico permite informar eliminaciones en `GET /sync`.

## Ejemplos por entidad

Crear favorito:

```json
{
  "operation_id": "44444444-4444-4444-4444-444444444444",
  "entity": "favorites",
  "action": "CREATE",
  "local_id": "favorite-temp-1",
  "server_id": null,
  "payload": {"favorite_type": "dish", "ref_id": "server-dish-id"},
  "version": null,
  "created_at": "2026-05-09T20:00:00Z"
}
```

Actualizar preferencias:

```json
{
  "operation_id": "55555555-5555-5555-5555-555555555555",
  "entity": "preferences",
  "action": "UPDATE",
  "local_id": "preferences-local",
  "server_id": "1",
  "payload": {
    "cuisine_types": ["tradicional"],
    "diet_types": ["regular"],
    "price_range": {"min": 10, "max": 40}
  },
  "version": 1,
  "created_at": "2026-05-09T20:00:00Z"
}
```

Crear resena:

```json
{
  "operation_id": "66666666-6666-6666-6666-666666666666",
  "entity": "reviews",
  "action": "CREATE",
  "local_id": "review-temp-1",
  "server_id": null,
  "payload": {
    "dish_id": "server-dish-id",
    "rating": 5,
    "comment": "Muy buen plato",
    "is_public": true
  },
  "version": null,
  "created_at": "2026-05-09T20:00:00Z"
}
```

Actualizar disponibilidad:

```json
{
  "operation_id": "22222222-2222-2222-2222-222222222222",
  "entity": "chef_availability",
  "action": "UPDATE",
  "local_id": "availability-local",
  "server_id": "1",
  "payload": {
    "is_active": true,
    "weekly_schedule": [],
    "accept_delivery": true,
    "accept_pickup": true,
    "simultaneous_orders_limit": 10
  },
  "version": 1,
  "created_at": "2026-05-09T20:00:00Z"
}
```

## Duplicados e idempotencia

Cada operacion usa `operation_id` como clave idempotente. Si el frontend reintenta el mismo envio, el backend consulta `sync_operations` y devuelve el resultado previamente guardado sin crear, actualizar ni eliminar dos veces.

## Conflictos

La estrategia de Sprint 1 es conservadora:

- si `version` del servidor es mayor a la version enviada por el cliente, hay conflicto
- si `updated_at` del servidor es posterior a `last_sync`, hay conflicto
- el backend no sobrescribe automaticamente datos del servidor

La respuesta incluye `server_data` y `client_data` para que el frontend pueda mostrar una resolucion manual o aplicar reglas en un sprint posterior.

## Contrato PWA recomendado para frontend

El frontend debe implementar:

- `manifest.json`
- service worker registrado al iniciar la app
- cache de assets principales y pantalla offline basica
- IndexedDB para guardar datos y operaciones pendientes
- cola local con `operation_id` UUID
- detector `online/offline`
- funcion `pushPendingOperations()` hacia `POST /api/v1/sync/`
- funcion `pullChanges(lastSync)` hacia `GET /api/v1/sync/?lastSync=...`

## Limitaciones para siguientes sprints

- La resolucion de conflictos es informativa, no automatica.
- No hay sincronizacion de pagos, autenticacion, notificaciones, pedidos, carrito, ventas ni reservas.
- La sincronizacion de resenas se limita a resenas creadas por el usuario autenticado y a resenas relacionadas con el cocinero autenticado.
- No se implemento frontend PWA en este repositorio porque no existe proyecto cliente aqui.
- La cola offline, IndexedDB y service worker deben vivir en el frontend.

## Justificacion tecnica

El sistema implementa una arquitectura offline-first para permitir el uso de funcionalidades criticas sin conexion o con conectividad inestable. En el frontend, la PWA permite cargar la aplicacion y almacenar operaciones localmente. En el backend, los endpoints de sincronizacion procesan las operaciones pendientes de forma idempotente, validada y controlada. Esto permite que los datos creados o modificados sin conexion se integren posteriormente a la base de datos central cuando la conexion sea restablecida.
