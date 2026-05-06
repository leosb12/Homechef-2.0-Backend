from rest_framework import serializers

from modules.storage_uploads.models import UploadedFile


class UploadedFileSerializer(serializers.ModelSerializer):
    owner = serializers.SerializerMethodField()

    class Meta:
        model = UploadedFile
        fields = (
            "id",
            "owner",
            "storage_bucket",
            "file_name",
            "file_path",
            "public_url",
            "mime_type",
            "size",
            "file_type",
            "created_at",
        )

    def get_owner(self, obj):
        return str(obj.owner.supabase_user_id)
