import json
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from planner.errors import PlanningError
from planner.geometry import validate_coordinate
from planner.models import Station


class Command(BaseCommand):
    help = 'Import reviewed exact station coordinates with source URLs from a JSON sidecar.'

    def add_arguments(self, parser):
        parser.add_argument('json_file')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            with open(options['json_file']) as f:
                rows = json.load(f)
            if not isinstance(rows, list):
                raise ValueError('Expected a JSON array')
            for row in rows:
                lon, lat = validate_coordinate({'lat': row['latitude'], 'lon': row['longitude']})
                if not isinstance(row['source'], str) or not row['source'].startswith('https://'):
                    raise ValueError('Each coordinate requires an HTTPS source URL')
                updated = Station.objects.filter(pk=int(row['opis_id'])).update(latitude=lat, longitude=lon,
                    coordinate_source=row['source'], geocode_status='verified')
                if updated != 1:
                    raise ValueError(f"Unknown station ID: {row['opis_id']}")
        except (OSError, ValueError, KeyError, TypeError, PlanningError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f'Imported {len(rows)} reviewed coordinate records.'))
