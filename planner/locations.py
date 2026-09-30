"""Offline, exact city/state lookup. Never used to position fuel stations."""
import json
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache

from django.conf import settings

from .errors import PlanningError

STATES = dict(zip(
    'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split(),
    'Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|District of Columbia'.split('|'),
))


def normalize(value):
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', value.casefold().replace('saint ', 'st '))


@lru_cache(maxsize=1)
def place_index():
    index = defaultdict(set)
    with (settings.BASE_DIR / 'data/us-places.json').open() as file:
        for city, state, lat, lon in json.load(file):
            point = (lon, lat)
            for name in (city, city + state, city + STATES[state]):
                index[normalize(name)].add(point)
    if index.get('newyorkny'):
        index['newyorkcityny'] = index['newyorkny']
        index['newyorkcitynewyork'] = index['newyorkny']
    return index


def local_place(query):
    query = re.sub(r'(?:,\s*|\s+)(?:USA|US|United States(?: of America)?)\s*$', '', query, flags=re.I)
    matches = place_index().get(normalize(query), set())
    if len(matches) > 1:
        raise PlanningError('ambiguous_location', 'This place name is ambiguous. Include its state, or use coordinates.', 400)
    return next(iter(matches), None)
