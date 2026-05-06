import re
from rest_framework import serializers

ROLE_CHOICES = ("CLIENTE", "COCINERO")


def validate_password_rules(password: str):
    if len(password) < 8:
        raise serializers.ValidationError("La contrasena debe tener al menos 8 caracteres.")
    if not re.search(r"[A-Z]", password):
        raise serializers.ValidationError("La contrasena debe incluir una letra mayuscula.")
    if not re.search(r"[a-z]", password):
        raise serializers.ValidationError("La contrasena debe incluir una letra minuscula.")
    if not re.search(r"[0-9]", password):
        raise serializers.ValidationError("La contrasena debe incluir un numero.")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise serializers.ValidationError("La contrasena debe incluir un caracter especial.")


class RegisterSerializer(serializers.Serializer):
    supabase_user_id = serializers.UUIDField()
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=ROLE_CHOICES)
    password = serializers.CharField(write_only=True, required=False)
    password_confirm = serializers.CharField(write_only=True, required=False)
    accept_terms = serializers.BooleanField()
    chef_specialties = serializers.CharField(required=False, allow_blank=True)
    chef_latitude = serializers.FloatField(required=False)
    chef_longitude = serializers.FloatField(required=False)
    chef_schedule = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs["accept_terms"]:
            raise serializers.ValidationError({"accept_terms": "Debes aceptar terminos y condiciones."})

        password = attrs.get("password")
        password_confirm = attrs.get("password_confirm")
        if password or password_confirm:
            if password != password_confirm:
                raise serializers.ValidationError({"password_confirm": "La confirmacion no coincide."})
            validate_password_rules(password)

        if attrs["role"] == "COCINERO":
            missing = []
            for key in ("chef_specialties", "chef_schedule"):
                if not attrs.get(key):
                    missing.append(key)
            if attrs.get("chef_latitude") is None:
                missing.append("chef_latitude")
            if attrs.get("chef_longitude") is None:
                missing.append("chef_longitude")
            if missing:
                raise serializers.ValidationError(
                    {"chef_profile": f"Faltan datos iniciales de cocinero: {', '.join(missing)}"}
                )
            lat = attrs.get("chef_latitude")
            lng = attrs.get("chef_longitude")
            if lat is not None and (lat < -90 or lat > 90):
                raise serializers.ValidationError({"chef_latitude": "Latitud invalida."})
            if lng is not None and (lng < -180 or lng > 180):
                raise serializers.ValidationError({"chef_longitude": "Longitud invalida."})
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class RecoverPasswordRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class RecoverPasswordConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)
    password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "La confirmacion no coincide."})
        validate_password_rules(attrs["password"])
        return attrs


class UpdateProfileSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=100, required=False)
    last_name = serializers.CharField(max_length=100, required=False)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    address = serializers.CharField(max_length=255, required=False, allow_blank=True)
    notify_gmail = serializers.BooleanField(required=False)
    notify_push = serializers.BooleanField(required=False)


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "La confirmacion no coincide."})
        validate_password_rules(attrs["new_password"])
        return attrs
