"""Rebuild bundled reference data from public first-party sources.

Run with the project's Python environment. This is OFFLINE preparation, never
part of an API request. Station matching requires chain, store number, city and
state; unmatched records are left unresolved instead of inventing coordinates.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

from shapely import make_valid, union_all
from shapely.geometry import Point, mapping, shape

ROOT = Path(__file__).resolve().parents[1]
PILOT = "https://locations.pilotflyingj.com/search"
CENSUS = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0"
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip"
TA_EXPORT = "https://www.ta-petro.com/api/locationsdata/download/"
LOVES_EXPORT = "https://www.loves.com/api/fetch_stores"


def match_loves(rows, locations, boundary):
    """Match official Love's locations without guessing identity from proximity."""
    index = defaultdict(list)
    for location in locations:
        if location.get('isLoveStore') is not True:
            continue
        key = (str(location['number']), normalized(location['city']), location['state'])
        index[key].append(location)
    matched = {}
    for row in rows:
        name = normalized(row['Truckstop Name'])
        number = re.search(r'#\s*(\d+)', row['Truckstop Name'])
        if not name.startswith('loves') or not number:
            continue
        key = (str(int(number[1])), normalized(row['City']), row['State'].strip())
        options = index.get(key, [])
        if len(options) != 1:
            continue
        location = options[0]
        lat, lon = float(location['latitude']), float(location['longitude'])
        if not boundary.covers(Point(lon, lat)):
            continue
        identifier = int(row['OPIS Truckstop ID'])
        matched[identifier] = {
            'opis_id': identifier, 'latitude': lat, 'longitude': lon,
            'source': LOVES_EXPORT, 'operator_site_id': str(location['number']),
            'operator_name': location['name'], 'operator_address': location['address1'],
            'match': 'operator chain + store number + city + state',
        }
    return matched


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold().replace("saint", "st"))


def pilot_brand(name):
    name = normalized(name)
    for prefix in ("flyingj", "pilot", "one9", "mrfuel", "pride", "stamart", "xpress", "eztrip"):
        if name.startswith(prefix):
            return prefix
    return None


def download_pilot(directory):
    directory.mkdir(parents=True, exist_ok=True)
    offset, count = 0, 1
    profiles = []
    while offset < count:
        path = directory / f"pilot-{offset}.json"
        if path.exists():
            page = json.loads(path.read_text())
        else:
            request = Request(f"{PILOT}?per=50&offset={offset}", headers={
                "Accept": "application/json", "User-Agent": "FuelRouteAssessment/1.1"})
            with urlopen(request, timeout=45) as response:
                page = json.load(response)
            path.write_text(json.dumps(page))
            time.sleep(1.1)
        result = page["response"]
        count = result["count"]
        entities = result["entities"]
        if not entities:
            raise ValueError("Incomplete Pilot location download")
        profiles.extend(entity["profile"] for entity in entities)
        offset += len(entities)
        print(f"Pilot locations: {offset}/{count}", flush=True)
    return profiles


def match_pilot(rows, profiles, boundary):
    index = defaultdict(list)
    for profile in profiles:
        address = profile["address"]
        if address["countryCode"] != "US" or not profile.get("c_liveOnPages"):
            continue
        number = profile.get("c_externalStoreNumber", profile.get("c_siteID", ""))
        key = (pilot_brand(profile["name"]), str(number), normalized(address["city"]), address["region"])
        index[key].append(profile)
    matched = {}
    for row in rows:
        number = re.search(r"#\s*(\d+)", row["Truckstop Name"])
        if not number:
            continue
        key = (pilot_brand(row["Truckstop Name"]), number[1], normalized(row["City"]), row["State"].strip())
        choices = index.get(key, []) if key[0] else []
        if len(choices) != 1:
            continue
        profile = choices[0]
        # Use the operator's road-access coordinate when supplied, not its city center.
        coordinate = profile.get("yextRoutableCoordinate") or profile.get("routableCoordinate") or profile.get("yextDisplayCoordinate")
        if not coordinate or not boundary.covers(Point(coordinate["long"], coordinate["lat"])):
            continue
        matched[int(row["OPIS Truckstop ID"])] = {
            "opis_id": int(row["OPIS Truckstop ID"]),
            "latitude": coordinate["lat"], "longitude": coordinate["long"],
            "source": profile["c_pagesURL"],
            "match": "operator chain + store number + city + state",
            "operator_name": profile.get("c_pagesName", profile["name"]),
            "operator_address": profile["address"]["line1"],
        }
    return matched


def build_places(path):
    with zipfile.ZipFile(path) as archive:
        raw = archive.read(archive.namelist()[0]).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw), delimiter="|" if "|" in raw.splitlines()[0] else "\t")
    places = []
    for row in reader:
        row = {key.strip(): value.strip() for key, value in row.items()}
        if row["USPS"] in {"PR", "GU", "VI", "AS", "MP"}:
            continue
        name = re.sub(r" (city and borough|municipality|unified government|consolidated government|metropolitan government|borough|city|town|village|CDP)$", "", row["NAME"])
        places.append([name, row["USPS"], float(row["INTPTLAT"]), float(row["INTPTLONG"])])
    return sorted(places)


def read_xlsx(path):
    """Read the official flat XLSX export using stdlib; retain blank columns."""
    namespace = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(node.text or '' for node in item.findall('.//m:t', namespace))
                       for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('m:si', namespace)]
        rows = []
        for row in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('.//m:row', namespace):
            values = {}
            for cell in row.findall('m:c', namespace):
                node = cell.find('m:v', namespace)
                value = node.text if node is not None else ''.join(t.text or '' for t in cell.findall('.//m:t', namespace))
                values[re.sub(r'\d', '', cell.get('r'))] = strings[int(value)] if cell.get('t') == 's' else value
            rows.append(values)
    return [{name: row.get(column, '') for column, name in rows[0].items()} for row in rows[1:]]


def match_ta(rows, locations, boundary, state_codes):
    def brand(name):
        return 'ta' if re.match(r'^TA(?:\s|$)', name, flags=re.I) else 'petro' if name.upper().startswith('PETRO ') else None

    def roads(address):
        return set(re.findall(r'\bI[-\s]*(\d+)', address.upper())), set(re.findall(r'EXIT\s*(\d+)', address.upper()))

    index = defaultdict(list)
    for location in locations:
        state = state_codes.get(normalized(location['State']))
        index[(brand(location['Location']), normalized(location['City']), state)].append(location)
    matched = {}
    for row in rows:
        key = (brand(row['Truckstop Name']), normalized(row['City']), row['State'].strip())
        options = index.get(key, []) if key[0] else []
        if len(options) != 1:
            continue
        location = options[0]
        number = re.search(r'#\s*(\d+)', row['Truckstop Name'])
        exact_id = number and int(number[1]) == int(location['Site ID'])
        route, exits = roads(row['Address'])
        other_route, other_exits = roads(location['Directions'])
        exact_exit = bool(route & other_route and exits & other_exits)
        exact_name = normalized(location['Location']) in normalized(row['Truckstop Name'])
        if not (exact_id or exact_exit or exact_name):
            continue
        lat, lon = float(location['Latitude']), float(location['Longitude'])
        if not boundary.covers(Point(lon, lat)):
            continue
        matched[int(row['OPIS Truckstop ID'])] = {
            'opis_id': int(row['OPIS Truckstop ID']), 'latitude': lat, 'longitude': lon,
            'source': TA_EXPORT, 'operator_site_id': location['Site ID'],
            'operator_name': location['Location'], 'operator_address': location['Address'],
            'match': 'unique chain + city + state, corroborated by store number, named location or interstate exit',
        }
    return matched


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census-states", type=Path, required=True)
    parser.add_argument("--gazetteer", type=Path, required=True)
    parser.add_argument("--pilot-cache", type=Path, required=True)
    parser.add_argument("--ta-export", type=Path)
    parser.add_argument("--loves-export", type=Path, help="JSON from Love's public location-search export")
    args = parser.parse_args()
    features = json.loads(args.census_states.read_text())["features"]
    if len(features) != 51:
        raise ValueError("Expected the 50 states plus DC")
    boundary = union_all([make_valid(shape(feature["geometry"])) for feature in features])
    boundary_file = {"type": "Feature", "properties": {
        "source": CENSUS, "description": "US Census state boundaries, 50 states and DC; generalized to 0.0001 degrees"},
        "geometry": mapping(boundary)}
    (ROOT / "data/usa.geojson").write_text(json.dumps(boundary_file, separators=(",", ":")) + "\n")
    places = build_places(args.gazetteer)
    (ROOT / "data/us-places.json").write_text(json.dumps(places, separators=(",", ":")) + "\n")
    with (ROOT / "data/fuel-prices.csv").open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    profiles = download_pilot(args.pilot_cache)
    coordinates_path = ROOT / "data/verified-coordinates.json"
    coordinates = {row["opis_id"]: row for row in json.loads(coordinates_path.read_text())}
    coordinates.update(match_pilot(rows, profiles, boundary))
    if args.ta_export:
        codes = {normalized(feature['properties']['NAME']): feature['properties']['STUSAB'] for feature in features}
        coordinates.update(match_ta(rows, read_xlsx(args.ta_export), boundary, codes))
    if args.loves_export:
        coordinates.update(match_loves(rows, json.loads(args.loves_export.read_text())['stores'], boundary))
    coordinates_path.write_text(json.dumps(sorted(coordinates.values(), key=lambda row: row["opis_id"]), indent=2) + "\n")
    provenance = {
        "station_sources": sorted({PILOT, *(row['source'] for row in coordinates.values() if row['source'] in {TA_EXPORT, LOVES_EXPORT})}), "matching": "See each coordinate record's match field; ambiguous matches excluded.",
        "station_coordinates": len(coordinates), "places": len(places),
        "boundary_source": CENSUS, "place_source": GAZETTEER,
        "price_sha256": hashlib.sha256((ROOT / "data/fuel-prices.csv").read_bytes()).hexdigest(),
        "notes": "Coordinates only are enriched. All prices remain from the assessment CSV. Gazetteer points are used only for requested city endpoints, never for station locations."
    }
    (ROOT / "data/provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
