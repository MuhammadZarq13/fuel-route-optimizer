from django.db import models


class FuelStation(models.Model):
    """A truck stop / fuel station with a known retail diesel price.

    Loaded from the OPIS fuel-price CSV via the `load_fuel_prices`
    management command. Coordinates are resolved once, offline, from a
    City+State geocode cache (see fuel/geocode_cache.json) rather than
    geocoded per station at request time.
    """

    opis_id = models.PositiveIntegerField(db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2)
    rack_id = models.PositiveIntegerField()
    price_per_gallon = models.DecimalField(max_digits=6, decimal_places=3)

    # Null when the City+State pair could not be geocoded.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["state", "city"]),
            models.Index(fields=["latitude", "longitude"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) - ${self.price_per_gallon}"
