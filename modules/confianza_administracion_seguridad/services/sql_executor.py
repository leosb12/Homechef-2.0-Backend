from django.db import connection
import re
from decimal import Decimal
from datetime import date, datetime

class SQLExecutor:
    """
    Ejecuta consultas SQL crudas con validación estricta de seguridad.
    """

    @staticmethod
    def is_safe_query(sql: str) -> bool:
        # Remover comentarios y espacios
        clean_sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
        clean_sql = re.sub(r'/\*.*?\*/', '', clean_sql, flags=re.DOTALL)
        clean_sql = clean_sql.strip().upper()
        
        # Verificar que inicie con SELECT (y opcionalmente WITH para CTEs)
        if not (clean_sql.startswith("SELECT") or clean_sql.startswith("WITH")):
            return False
            
        # Bloquear palabras clave peligrosas
        dangerous_keywords = [
            "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", 
            "CREATE", "TRUNCATE", "GRANT", "REVOKE", "EXEC", 
            "EXECUTE", "MERGE"
        ]
        
        # Búsqueda por palabras completas
        for kw in dangerous_keywords:
            if re.search(rf'\b{kw}\b', clean_sql):
                return False
                
        return True

    @staticmethod
    def validate_tables_and_joins(sql: str) -> None:
        # Remover comentarios
        clean_sql = re.sub(r'--.*$', '', sql, flags=re.MULTILINE)
        clean_sql = re.sub(r'/\*.*?\*/', '', clean_sql, flags=re.DOTALL)
        clean_sql = clean_sql.strip()

        # 1. Obtener nombres de tablas permitidas dinámicamente desde SchemaBuilder
        from django.apps import apps
        from .schema_builder import SchemaBuilder
        allowed_tables = set()
        for app_label, model_name in SchemaBuilder.TARGET_MODELS:
            try:
                model = apps.get_model(app_label, model_name)
                allowed_tables.add(model._meta.db_table.lower())
            except LookupError:
                pass

        # 2. Encontrar CTEs definidos en WITH (estos están permitidos dentro de la misma consulta)
        cte_names = set(re.findall(r'\bWITH\s+([a-zA-Z0-9_]+)\s+AS\b', clean_sql, re.IGNORECASE))
        cte_names.update(re.findall(r',\s*([a-zA-Z0-9_]+)\s+AS\b', clean_sql, re.IGNORECASE))
        cte_names = {name.lower() for name in cte_names}

        # 3. Encontrar tablas referenciadas después de FROM y JOIN (evitando funciones como jsonb_array_elements_text)
        referenced_tables = []
        matches = re.finditer(r'\b(?:FROM|JOIN)\s+\b([a-zA-Z0-9_\"\.]+)\b(?!\s*\()', clean_sql, re.IGNORECASE)
        for match in matches:
            table_name = match.group(1).strip('"').lower()
            if '.' in table_name:
                table_name = table_name.split('.')[-1]
            referenced_tables.append(table_name)

        # 4. Validar que todas las tablas referenciadas estén permitidas
        for table in referenced_tables:
            if table in cte_names or table in ('select', 'values'):
                continue
            if table not in allowed_tables:
                raise ValueError(f"Acceso denegado a la tabla '{table}'. Solo se permiten tablas del esquema de HomeChef.")

        # 5. Ejecutar EXPLAIN en PostgreSQL para validar relaciones, joins y tipos de datos
        from django.db import connection
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"EXPLAIN {sql}")
        except Exception as e:
            raise ValueError(f"La consulta SQL no es válida estructuralmente: {str(e)}")

    @staticmethod
    def execute(sql: str) -> list[dict]:
        if not SQLExecutor.is_safe_query(sql):
            raise ValueError("Query contains forbidden operations. Only SELECT is allowed.")
            
        # Ejecutar validaciones adicionales de tablas y joins (EXPLAIN)
        SQLExecutor.validate_tables_and_joins(sql)
            
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [col[0] for col in cursor.description]
            
            # Convertimos los resultados a un listado de diccionarios
            results = []
            for row in cursor.fetchall():
                row_dict = {}
                for col_name, value in zip(columns, row):
                    if isinstance(value, Decimal):
                        row_dict[col_name] = float(value)
                    elif isinstance(value, (datetime, date)):
                        row_dict[col_name] = value.isoformat()
                    else:
                        row_dict[col_name] = value
                results.append(row_dict)
                
            return results
