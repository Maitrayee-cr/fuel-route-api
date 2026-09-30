from django.urls import path
from planner import views

urlpatterns = [
    path('', views.home, name='home'),
    path('api/health/', views.health),
    path('api/routes/', views.create_route),
    path('api/routes/<uuid:trip_id>/', views.route_detail),
    path('maps/<uuid:trip_id>/', views.route_map, name='route-map'),
]
