import mimetypes
import re
from pathlib import Path
from uuid import uuid4

import requests
from django.conf import settings

from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile
from modules.storage_uploads.models import UploadedFile


class SupabaseStorageError(RuntimeError):
    pass


class SupabaseStorageClient:
    def __init__(self, bucket: str | None = None):
        self.url = settings.SUPABASE_URL.rstrip("/")
        self.service_key = settings.SUPABASE_SERVICE_ROLE_KEY
        self.bucket = bucket or settings.SUPABASE_BUCKET
        if not self.url or not self.service_key:
            raise SupabaseStorageError("Supabase Storage no esta configurado.")

    def upload_bytes(self, path: str, content: bytes, content_type: str, upsert: bool = False):
        encoded_path = "/".join(_quote_path_part(part) for part in path.split("/"))
        upload_url = f"{self.url}/storage/v1/object/{self.bucket}/{encoded_path}"
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": content_type,
            "cache-control": "3600",
            "x-upsert": "true" if upsert else "false",
        }
        response = requests.post(upload_url, headers=headers, data=content, timeout=30)
        if response.status_code >= 400:
            raise SupabaseStorageError(f"Supabase Storage rechazo el upload: {response.text}")
        return response.json() if response.content else {}

    def public_url(self, path: str) -> str:
        encoded_path = "/".join(_quote_path_part(part) for part in path.split("/"))
        return f"{self.url}/storage/v1/object/public/{self.bucket}/{encoded_path}"

    def signed_url(self, path: str, expires_in: int = 3600) -> str:
        encoded_path = "/".join(_quote_path_part(part) for part in path.split("/"))
        signed_url = f"{self.url}/storage/v1/object/sign/{self.bucket}/{encoded_path}"
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(signed_url, headers=headers, json={"expiresIn": expires_in}, timeout=15)
        if response.status_code >= 400:
            raise SupabaseStorageError(f"No se pudo crear signed URL: {response.text}")
        data = response.json()
        path_or_url = data.get("signedURL") or data.get("signedUrl") or ""
        if path_or_url.startswith("http"):
            return path_or_url
        return f"{self.url}{path_or_url}"


class StorageUploadService:
    def __init__(self):
        self.client = SupabaseStorageClient()

    def _ensure_bucket(self, client: SupabaseStorageClient):
        url = f"{client.url}/storage/v1/bucket"
        headers = {
            "apikey": client.service_key,
            "Authorization": f"Bearer {client.service_key}",
            "Content-Type": "application/json",
        }
        res = requests.get(f"{url}/{client.bucket}", headers=headers, timeout=10)
        if res.status_code == 404 or "Bucket not found" in res.text:
            requests.post(url, headers=headers, json={"id": client.bucket, "name": client.bucket, "public": True}, timeout=10)

    def upload_for_user(self, user, file_obj, file_type: str = "general") -> UploadedFile:
        owner = UserProfile.objects.get(supabase_user_id=user.id)
        return self.upload_for_owner(owner, file_obj, file_type=file_type)

    def upload_for_owner(self, owner: UserProfile, file_obj, file_type: str = "general") -> UploadedFile:
        original_name = Path(file_obj.name or "upload.bin").name
        safe_name = _safe_filename(original_name)
        clean_type = _safe_path_part(file_type or "general")
        path = f"users/{owner.supabase_user_id}/{clean_type}/{uuid4()}_{safe_name}"

        content = b"".join(chunk for chunk in file_obj.chunks())
        content_type = file_obj.content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        self.client.upload_bytes(path=path, content=content, content_type=content_type, upsert=False)
        public_url = self.client.public_url(path)

        return UploadedFile.objects.create(
            owner=owner,
            storage_bucket=self.client.bucket,
            file_name=original_name,
            file_path=path,
            public_url=public_url,
            mime_type=content_type,
            size=file_obj.size or len(content),
            file_type=clean_type,
        )




def _safe_filename(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return stem or "upload.bin"


def _safe_path_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value).strip().lower()).strip("-")
    return cleaned or "general"


def _quote_path_part(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
