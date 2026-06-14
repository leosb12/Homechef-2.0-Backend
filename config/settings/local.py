from .base import *
DEBUG=True

ALLOWED_HOSTS = [*ALLOWED_HOSTS, "192.168.1.18", "0.0.0.0"]

if not OSM_ROUTING_BASE_URL:
    OSM_ROUTING_BASE_URL = "https://router.project-osrm.org"
