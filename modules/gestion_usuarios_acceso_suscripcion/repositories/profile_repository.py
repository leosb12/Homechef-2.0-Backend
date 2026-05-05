from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ASCENDING

from shared.database.mongo_client import get_collection


class ProfileRepository:
    def __init__(self):
        self.user_profile = get_collection("users_profile")
        self.chef_profile = get_collection("chef_profiles")
        self.recovery_tokens = get_collection("auth_recovery_tokens")
        self.audit_events = get_collection("audit_events")
        self.recovery_tokens.create_index([("token", ASCENDING)], unique=True)
        self.recovery_tokens.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0)

    def get_profile(self, user_id: str):
        return self.user_profile.find_one({"user_id": user_id}) or {}

    def save_profile(self, user_id: str, payload: dict):
        data = {
            **payload,
            "user_id": user_id,
            "updated_at": datetime.now(timezone.utc),
        }
        self.user_profile.update_one({"user_id": user_id}, {"$set": data}, upsert=True)
        return self.get_profile(user_id)

    def save_chef_profile(self, user_id: str, payload: dict):
        data = {**payload, "user_id": user_id, "updated_at": datetime.now(timezone.utc)}
        self.chef_profile.update_one({"user_id": user_id}, {"$set": data}, upsert=True)
        return self.get_chef_profile(user_id)

    def get_chef_profile(self, user_id: str):
        return self.chef_profile.find_one({"user_id": user_id})

    def create_recovery_token(self, email: str):
        token = f"rcv-{uuid4()}"
        self.recovery_tokens.insert_one(
            {
                "token": token,
                "email": email.lower().strip(),
                "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
                "created_at": datetime.now(timezone.utc),
            }
        )
        return token

    def consume_recovery_token(self, token: str):
        data = self.recovery_tokens.find_one({"token": token})
        if not data:
            return None
        if data["expires_at"] < datetime.now(timezone.utc):
            self.recovery_tokens.delete_one({"token": token})
            return None
        self.recovery_tokens.delete_one({"token": token})
        return data["email"]

    def log_event(self, event: str, details: dict):
        self.audit_events.insert_one(
            {
                "event": event,
                "details": details,
                "at": datetime.now(timezone.utc),
            }
        )
