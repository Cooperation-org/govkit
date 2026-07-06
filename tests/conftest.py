import pytest
from django.contrib.auth import get_user_model

from apps.orgs.models import Membership, MembershipRole, Org, ValuationConfig


@pytest.fixture
def user_factory(db):
    User = get_user_model()
    counter = {"n": 0}

    def make(email=None, password="pw12345!", **kwargs):
        counter["n"] += 1
        email = email or f"user{counter['n']}@example.com"
        user = User.objects.create_user(email=email, password=password, **kwargs)
        return user

    return make


@pytest.fixture
def org_factory(db):
    counter = {"n": 0}

    def make(slug=None, **kwargs):
        counter["n"] += 1
        slug = slug or f"org{counter['n']}"
        org = Org.objects.create(slug=slug, display_name=kwargs.pop("display_name", slug))
        ValuationConfig.objects.create(org=org)
        return org

    return make


@pytest.fixture
def membership_factory(db):
    def make(org, user, role=MembershipRole.MEMBER, **kwargs):
        return Membership.objects.create(org=org, user=user, role=role, **kwargs)

    return make
