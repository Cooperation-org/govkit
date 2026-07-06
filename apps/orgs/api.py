"""
DRF router for orgs — STUB.

Feature agents (orgs/onboarding): register viewsets here, e.g.

    from rest_framework import viewsets
    from .models import Org
    router.register(r"orgs", OrgViewSet, basename="org")

Every UI action must have a matching DRF endpoint (API-first). Scope every queryset to
request.org — see apps/orgs/scoping.py.
"""

from rest_framework.routers import DefaultRouter

router = DefaultRouter()

urlpatterns = router.urls
