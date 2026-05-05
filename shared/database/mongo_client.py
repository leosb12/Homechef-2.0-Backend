import os
from functools import lru_cache

from pymongo import MongoClient


@lru_cache(maxsize=1)
def get_mongo_client():
    uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017/homechef")
    return MongoClient(uri)


@lru_cache(maxsize=1)
def get_database():
    db_name = os.getenv("MONGODB_DB", "homechef")
    return get_mongo_client()[db_name]


def get_collection(name: str):
    return get_database()[name]
