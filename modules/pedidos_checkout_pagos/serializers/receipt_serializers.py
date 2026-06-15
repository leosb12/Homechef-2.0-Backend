from rest_framework import serializers


class ReceiptDownloadQuerySerializer(serializers.Serializer):
    file_format = serializers.ChoiceField(
        choices=["pdf", "html", "docx"],
        required=False,
        default="pdf",
    )
