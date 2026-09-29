from rest_framework import serializers


class TripPlanRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=255, trim_whitespace=True)
    finish = serializers.CharField(max_length=255, trim_whitespace=True)


class FuelStopSerializer(serializers.Serializer):
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    price_per_gallon = serializers.DecimalField(max_digits=6, decimal_places=3)
    distance_along_route_miles = serializers.FloatField()
    gallons_purchased = serializers.FloatField()
    cost = serializers.FloatField()


class RouteGeometrySerializer(serializers.Serializer):
    type = serializers.CharField(default="LineString")
    coordinates = serializers.ListField(child=serializers.ListField(child=serializers.FloatField()))


class TripPlanResponseSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField()
    route = RouteGeometrySerializer()
    fuel_stops = FuelStopSerializer(many=True)
    total_gallons_purchased = serializers.FloatField()
    total_fuel_cost = serializers.FloatField()
