import json
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from .errors import PlanningError
from .models import Station, Trip
from .service import plan_trip


@require_GET
def home(request):
    return render(request, 'planner/home.html')


@require_GET
def health(request):
    return JsonResponse({'status': 'ok', 'stations': Station.objects.count(),
                         'verified_stations': Station.objects.filter(geocode_status='verified').count()})


@csrf_exempt  # Public stateless JSON API; no cookie/session authentication is used.
@require_POST
def create_route(request):
    if request.content_type != 'application/json':
        return JsonResponse({'error': {'code': 'unsupported_media_type', 'message': 'Use application/json.'}}, status=415)
    try:
        if len(request.body) > 16384:
            raise RequestDataTooBig
        payload = json.loads(request.body)
        result = plan_trip(payload)
    except RequestDataTooBig:
        return JsonResponse({'error': {'code': 'request_too_large', 'message': 'Request body must be at most 16 KiB.'}}, status=413)
    except (ValueError, UnicodeDecodeError) as exc:
        return JsonResponse({'error': {'code': 'invalid_json', 'message': 'Provide valid JSON.'}}, status=400)
    except PlanningError as exc:
        return JsonResponse({'error': {'code': exc.code, 'message': exc.message}}, status=exc.status)
    trip = Trip.objects.create(result=result)
    result = {**result, 'id': str(trip.id), 'map_url': request.build_absolute_uri(reverse('route-map', args=[trip.id]))}
    return JsonResponse(result, status=201, json_dumps_params={'allow_nan': False})


@require_GET
def route_detail(request, trip_id):
    trip = Trip.objects.filter(id=trip_id).first()
    if trip is None:
        return JsonResponse({'error': {'code': 'route_not_found', 'message': 'No saved route has this ID.'}}, status=404)
    return JsonResponse({**trip.result, 'id': str(trip.id), 'map_url': request.build_absolute_uri(reverse('route-map', args=[trip.id]))})


@require_GET
def route_map(request, trip_id):
    trip = get_object_or_404(Trip, id=trip_id)
    return render(request, 'planner/map.html', {'trip': trip.result})
