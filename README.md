# HomeChef Backend

Monolito modular de negocio con Django + DRF alineado a PUDS y 7 modulos funcionales.
Persistencia de dominio y autenticacion del modulo 1 basada en MongoDB.

## Instalacion
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## Ejecutar
```bash
python manage.py runserver 0.0.0.0:8000
```

## Variables
Revisar `.env.example`.

## MongoDB
- Configurar `MONGODB_URI` y `MONGODB_DB`.
- Colecciones iniciales: `auth_users`, `users_profile`, `chef_profiles`, `auth_recovery_tokens`, `audit_events`.
- No se usa SQLite para CU-01..CU-04.

## Modulos
- gestion_usuarios_acceso_suscripcion
- marketplace_platos
- gestion_cocinero
- asistencia_inteligente
- pedidos_checkout_pagos
- delivery_logistica
- confianza_administracion_seguridad

## Integracion con IA
Django valida JWT, rol, suscripcion y limites antes de llamar a `homechef-ai-service` con `X-AI-Service-Token`.
