import uuid
from django.db import models


class Station(models.Model):
    opis_id = models.PositiveIntegerField(primary_key=True)
    name = models.CharField(max_length=240)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price = models.DecimalField(max_digits=12, decimal_places=8)
    source_prices = models.JSONField(default=list)
    latitude = models.FloatField(null=True)
    longitude = models.FloatField(null=True)
    coordinate_source = models.TextField(blank=True)
    geocode_status = models.CharField(max_length=16, default='pending', db_index=True)
    geocode_result = models.JSONField(default=dict)

    class Meta:
        indexes = [models.Index(fields=['latitude', 'longitude'])]


class Trip(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    result = models.JSONField()


class ProviderGate(models.Model):
    """Database-backed cross-process rate limiter for this deployment."""
    provider = models.CharField(max_length=30, primary_key=True)
    next_allowed = models.FloatField(default=0)
