from rest_framework import serializers


class IAFunctionUseRequestSerializer(serializers.Serializer):
    funcion = serializers.CharField(max_length=80, trim_whitespace=True)


class IAAccessResponseSerializer(serializers.Serializer):
    permitido = serializers.BooleanField()
    codigo = serializers.CharField()
    mensaje = serializers.CharField()
