from django.core.management.base import BaseCommand, CommandError
from planner.errors import PlanningError
from planner.models import Station
from planner.providers import geocode_query


class Command(BaseCommand):
    help = 'Resumable offline geocoding. Results require review; never promotes city centroids to stations.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)
        parser.add_argument('--state')

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 2500:
            raise CommandError('Use a limit between 1 and 2500; budget within your provider quota.')
        qs = Station.objects.filter(geocode_status='pending').order_by('opis_id')
        if options['state']:
            qs = qs.filter(state=options['state'].upper())
        count = 0
        for station in qs[:options['limit']]:
            query = f'{station.name}, {station.address}, {station.city}, {station.state}, USA'
            try:
                data = geocode_query(query)
            except PlanningError as exc:
                raise CommandError(f'{exc.message} Progress has been saved; rerun to resume.') from exc
            station.geocode_result = data
            station.geocode_status = 'review'
            station.save(update_fields=['geocode_result', 'geocode_status'])
            count += 1
        self.stdout.write(f'{count} geocodes stored for review. Export with export_geocodes, verify station-level matches, then import_coordinates.')
