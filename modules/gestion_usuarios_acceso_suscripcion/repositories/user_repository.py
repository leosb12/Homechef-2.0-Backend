from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from django.contrib.auth.hashers import check_password, make_password
from pymongo import ASCENDING

from shared.database.mongo_client import get_collection


@dataclass
class MongoUser:
    id: str
    email: str
    first_name: str
    last_name: str
    role: str
    is_active: bool

    @property
    def is_authenticated(self):
        return True


class UserRepository:
    def __init__(self):
        self.collection = get_collection("auth_users")
        self.collection.create_index([("email", ASCENDING)], unique=True)

    def find_by_email(self, email: str):
        doc = self.collection.find_one({"email": email.lower().strip()})
        return self._to_user(doc) if doc else None

    def find_raw_by_email(self, email: str):
        return self.collection.find_one({"email": email.lower().strip()})

    def find_by_id(self, user_id: str):
        doc = self.collection.find_one({"_id": user_id})
        return self._to_user(doc) if doc else None

    def create_user(self, email: str, password: str, first_name: str, last_name: str, role: str, phone: str):
        user_id = str(uuid4())
        doc = {
            "_id": user_id,
            "email": email.lower().strip(),
            "password_hash": make_password(password),
            "first_name": first_name.strip(),
            "last_name": last_name.strip(),
            "phone": phone.strip(),
            "role": role,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        self.collection.insert_one(doc)
        return self._to_user(doc)

    def verify_credentials(self, email: str, password: str):
        doc = self.find_raw_by_email(email)
        if not doc:
            return None
        if not check_password(password, doc["password_hash"]):
            return None
        return self._to_user(doc)

    def update_password(self, user_id: str, password: str):
        self.collection.update_one(
            {"_id": user_id},
            {"$set": {"password_hash": make_password(password), "updated_at": datetime.now(timezone.utc)}},
        )

    def update_basic_data(self, user_id: str, payload: dict):
        payload["updated_at"] = datetime.now(timezone.utc)
        self.collection.update_one({"_id": user_id}, {"$set": payload})
        return self.find_by_id(user_id)

    def _to_user(self, doc):
        return MongoUser(
            id=doc["_id"],
            email=doc["email"],
            first_name=doc.get("first_name", ""),
            last_name=doc.get("last_name", ""),
            role=doc.get("role", ""),
            is_active=doc.get("is_active", True),
        )
