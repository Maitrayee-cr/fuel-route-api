import csv
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from planner.models import Station

US_STATES = set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split())


class Command(BaseCommand):
    help = 'Import the assessment CSV; deduplicate OPIS IDs and exclude non-US states.'

    def add_arguments(self, parser):
        parser.add_argument('csv_file')

    @transaction.atomic
    def handle(self, *args, **options):
        grouped = defaultdict(list)
        skipped = 0
        try:
            with open(options['csv_file'], newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                required = {'OPIS Truckstop ID', 'Truckstop Name', 'Address', 'City', 'State', 'Retail Price'}
                if not required <= set(reader.fieldnames or []):
                    raise CommandError('CSV headers do not match the assessment dataset.')
                for line, row in enumerate(reader, 2):
                    if row['State'].strip() not in US_STATES:
                        skipped += 1
                        continue
                    try:
                        price, station_id = Decimal(row['Retail Price']), int(row['OPIS Truckstop ID'])
                        if not price.is_finite() or not 0 < price < 100 or station_id < 1:
                            raise ValueError('invalid price or ID')
                    except (InvalidOperation, ValueError) as exc:
                        raise CommandError(f'Invalid ID or price at line {line}.') from exc
                    grouped[station_id].append((row, price))
        except OSError as exc:
            raise CommandError(str(exc)) from exc
        conflicts = 0
        for station_id, entries in grouped.items():
            identities = {(r['City'].casefold().strip(), r['State'].strip(), r['Address'].casefold().strip()) for r, _ in entries}
            if len(identities)>1:
                # ID remains stable, but any existing location must be re-reviewed.
                Station.objects.filter(pk=station_id).update(latitude=None, longitude=None, coordinate_source='', geocode_status='pending')
            prices = sorted(set(p for _, p in entries))
            conflicts += len(prices)>1
            row = entries[0][0]
            existing = Station.objects.filter(pk=station_id).first()
            address = row['Address'].strip()
            city, state = row['City'].strip(), row['State'].strip()
            defaults = {'name': row['Truckstop Name'].strip(), 'address': address, 'city': city, 'state': state,
                        'price': max(prices), 'source_prices': [str(p) for p in prices]}
            if existing and (existing.address, existing.city, existing.state) != (address, city, state):
                defaults.update(latitude=None, longitude=None, coordinate_source='', geocode_status='pending')
            Station.objects.update_or_create(opis_id=station_id, defaults=defaults)
        self.stdout.write(self.style.SUCCESS(f'Imported {len(grouped)} US station IDs; excluded {skipped} non-US rows; {conflicts} price conflicts (maximum retained).'))
