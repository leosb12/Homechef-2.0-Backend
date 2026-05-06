from django.db import models

from modules.gestion_usuarios_acceso_suscripcion.models import UserProfile


class UploadedFile(models.Model):
    owner = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="uploaded_files")
    storage_bucket = models.CharField(max_length=120)
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=1000, unique=True)
    public_url = models.URLField(max_length=1200, blank=True)
    mime_type = models.CharField(max_length=255, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    file_type = models.CharField(max_length=80, default="general")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "uploaded_files"
        indexes = [
            models.Index(fields=["owner", "file_type"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return self.file_path
