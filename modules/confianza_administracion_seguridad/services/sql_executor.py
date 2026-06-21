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
    def execute(sql: str) -> list[dict]:
        if not SQLExecutor.is_safe_query(sql):
            raise ValueError("Query contains forbidden operations. Only SELECT is allowed.")
            
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
