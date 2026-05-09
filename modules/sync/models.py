from django.db import models


class SyncOperation(models.Model):
    STATUS_SYNCED = "synced"
    STATUS_ERROR = "error"
    STATUS_CONFLICT = "conflict"

    operation_id = models.CharField(max_length=80, unique=True, db_index=True)
    device_id = models.CharField(max_length=120, db_index=True)
    entity = models.CharField(max_length=80)
    action = models.CharField(max_length=20)
    local_id = models.CharField(max_length=120, blank=True)
    server_id = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20)
    result = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sync_operations"
        indexes = [
            models.Index(fields=["device_id", "created_at"]),
            models.Index(fields=["entity", "server_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.operation_id} - {self.entity}:{self.action}"
