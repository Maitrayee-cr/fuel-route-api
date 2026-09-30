import json
from django.core.management.base import BaseCommand
from planner.models import Station


class Command(BaseCommand):
    help = 'Export geocoding candidates with original station identity for human review.'

    def handle(self, *args, **options):
        rows = [{'opis_id': s.opis_id, 'name': s.name, 'address': s.address, 'city': s.city,
                 'state': s.state, 'geocoding': s.geocode_result}
                for s in Station.objects.filter(geocode_status='review').order_by('opis_id')]
        self.stdout.write(json.dumps(rows, indent=2))
