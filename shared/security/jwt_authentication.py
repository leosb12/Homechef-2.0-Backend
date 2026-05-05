from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

from shared.database.mongo_client import get_collection


@dataclass
class MongoAuthUser:
    id: str
    email: str
    role: str
    first_name: str = ""
    last_name: str = ""
    is_active: bool = True
    is_authenticated: bool = True


def generate_access_token(user_id: str, email: str, role: str):
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": "access",
        "exp": datetime.now(timezone.utc) + timedelta(hours=2),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def generate_refresh_token(user_id: str, email: str, role: str):
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


class MongoJWTAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header.replace("Bearer ", "").strip()
        try:
            payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

        if payload.get("type") != "access":
            return None

        users = get_collection("auth_users")
        user_doc = users.find_one({"_id": payload["sub"]})
        if not user_doc or not user_doc.get("is_active", True):
            return None

        user = MongoAuthUser(
            id=user_doc["_id"],
            email=user_doc["email"],
            role=user_doc.get("role", ""),
            first_name=user_doc.get("first_name", ""),
            last_name=user_doc.get("last_name", ""),
            is_active=user_doc.get("is_active", True),
        )
        return (user, token)
