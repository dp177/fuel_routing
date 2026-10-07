from django.db import models


class FuelStation(models.Model):
    """Represents a commercial truckstop / fuel station.

    Coordinates represent approximate locations (e.g. city centroid) derived from
    the bundled US city gazetteer, as raw highway-exit addresses do not contain
    exact geographic coordinates.
    """

    station_id = models.IntegerField(
        unique=True,
        db_index=True,
        help_text="Unique OPIS Truckstop identifier.",
    )
    name = models.CharField(max_length=255, help_text="Truckstop business name.")
    address = models.CharField(max_length=255, help_text="Street or highway exit address.")
    city = models.CharField(max_length=128, db_index=True)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True, help_text="Wholesale terminal rack ID.")
    price_per_gallon = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        help_text="Retail diesel price in USD per gallon (lowest available rate).",
    )
    latitude = models.FloatField(
        null=True,
        blank=True,
        help_text="Approximate station latitude in degrees.",
    )
    longitude = models.FloatField(
        null=True,
        blank=True,
        help_text="Approximate station longitude in degrees.",
    )
    coordinate_source = models.CharField(
        max_length=32,
        default="city_centroid",
        help_text="Source / precision level of coordinates ('city_centroid', 'unresolved').",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["latitude", "longitude"], name="fuel_station_coords_idx"),
            models.Index(fields=["state", "city"], name="fuel_station_state_city_idx"),
        ]
        ordering = ["station_id"]

    def __str__(self) -> str:
        return f"[{self.station_id}] {self.name} - {self.city}, {self.state} (${self.price_per_gallon}/gal)"
