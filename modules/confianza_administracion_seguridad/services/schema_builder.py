from django.apps import apps
from django.db import models

class SchemaBuilder:
    """
    Extrae un DDL simplificado de los modelos relevantes de HomeChef.
    """
    TARGET_MODELS = [
        ('pedidos_checkout_pagos', 'Order'),
        ('pedidos_checkout_pagos', 'OrderItem'),
        ('gestion_cocinero', 'Dish'),
        ('gestion_usuarios_acceso_suscripcion', 'UserProfile'),
        ('gestion_usuarios_acceso_suscripcion', 'DeliveryProfile'),
        ('gestion_cocinero', 'ChefProfile'),
        ('marketplace_platos', 'MarketplaceReview'),
    ]

    @staticmethod
    def get_database_schema() -> str:
        schema_lines = []
        schema_lines.append("-- Esquema simplificado de base de datos de HomeChef\n")
        
        for app_label, model_name in SchemaBuilder.TARGET_MODELS:
            try:
                model = apps.get_model(app_label, model_name)
                table_name = model._meta.db_table
                schema_lines.append(f"CREATE TABLE {table_name} (")
                
                for field in model._meta.fields:
                    field_type = field.get_internal_type()
                    # Simplificamos los tipos para que la IA entienda
                    sql_type = "VARCHAR"
                    if field_type in ['IntegerField', 'BigAutoField', 'AutoField', 'PositiveIntegerField']:
                        sql_type = "INTEGER"
                    elif field_type in ['DecimalField', 'FloatField']:
                        sql_type = "DECIMAL"
                    elif field_type in ['DateTimeField', 'DateField']:
                        sql_type = "TIMESTAMP"
                    elif field_type == 'BooleanField':
                        sql_type = "BOOLEAN"
                    elif field_type == 'JSONField':
                        sql_type = "JSONB"
                        
                    is_pk = " PRIMARY KEY" if field.primary_key else ""
                    schema_lines.append(f"    {field.column} {sql_type}{is_pk},")
                
                schema_lines.append(");")
                schema_lines.append("") # Línea en blanco
            except LookupError:
                pass # El modelo no existe, omitir
        
        return "\n".join(schema_lines)
