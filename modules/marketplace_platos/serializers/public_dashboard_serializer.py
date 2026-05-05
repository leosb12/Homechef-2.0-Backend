from rest_framework import serializers


class PublicDishSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    image_url = serializers.CharField(required=False, allow_blank=True)
    approx_price = serializers.FloatField()
    chef_name = serializers.CharField()
    is_featured = serializers.BooleanField()
    is_available = serializers.BooleanField(required=False)
    distance_km = serializers.FloatField(required=False)
    rating = serializers.FloatField(required=False)
    popularity = serializers.IntegerField(required=False)
    cuisine_type = serializers.CharField(required=False)
    diet_type = serializers.CharField(required=False)


class PublicDashboardResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    message = serializers.CharField()
    dishes = PublicDishSerializer(many=True)
