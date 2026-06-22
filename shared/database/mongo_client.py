import os
from functools import lru_cache

from pymongo import MongoClient


MONGO_URI_ENV_NAMES = ("MONGODB_URI", "MONGO_URI", "MONGODB_URL", "ATLAS_URI")
MONGO_DB_ENV_NAMES = ("MONGODB_DATABASE", "MONGODB_DB", "MONGO_DB")


class MongoConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_mongo_client():
    uri = _configured_mongo_uri()
    if _is_local_mongo_uri(uri) and not _allow_local_mongo():
        raise MongoConfigurationError("MongoDB Atlas no esta configurado para auditoria IA. Revisa MONGODB_URI.")
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


@lru_cache(maxsize=1)
def get_database():
    db_name = _configured_mongo_database()
    return get_mongo_client()[db_name]


def get_collection(name: str):
    return get_database()[name]


def _configured_mongo_uri():
    for env_name in MONGO_URI_ENV_NAMES:
        value = os.getenv(env_name)
        if value:
            return value.strip().strip('"').strip("'")
    raise MongoConfigurationError("MongoDB Atlas no esta configurado para auditoria IA. Revisa MONGODB_URI.")


def _configured_mongo_database():
    for env_name in MONGO_DB_ENV_NAMES:
        value = os.getenv(env_name)
        if value:
            return value.strip().strip('"').strip("'")
    return "homechef_ia"


def _is_local_mongo_uri(uri):
    normalized = str(uri or "").lower()
    return "localhost" in normalized or "127.0.0.1" in normalized or "host.docker.internal" in normalized


def _allow_local_mongo():
    value = os.getenv("ALLOW_LOCAL_MONGO_FOR_AI_AUDIT") or os.getenv("ALLOW_LOCAL_MONGO") or ""
    return str(value).strip().lower() in {"1", "true", "yes", "y", "local"}
