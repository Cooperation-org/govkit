"""Org slugs stay inside what earnkit's add-team.yml will accept.

A slug over 31 characters produces a GovKit org whose team stack can never be
built: add-team.yml fails its opening assert and no Odoo DB, Taiga project,
amebo instance or Caddy route is ever created. That happened to a real venture
("Alonovo: Value Aligned Consumer Spending and Investing", 53 characters).
"""

import re

import pytest

from apps.orgs.forms import OnboardingForm
from apps.orgs.models import Org
from apps.orgs.slugs import MAX_SLUG_LENGTH, normalize_org_slug, unique_org_slug

# The pattern add-team.yml asserts on, verbatim.
ADD_TEAM_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")

LONG_NAME = "Alonovo: Value Aligned Consumer Spending and Investing"


def test_long_venture_name_gives_a_slug_add_team_accepts():
    slug = normalize_org_slug(LONG_NAME)
    assert len(slug) <= MAX_SLUG_LENGTH
    assert ADD_TEAM_SLUG.match(slug)


def test_truncation_never_leaves_a_trailing_hyphen():
    # "and" ends exactly at the cap, so the cut lands on the following hyphen.
    assert not normalize_org_slug("Value Aligned Consumer Spending and Investing").endswith("-")


def test_unusable_name_returns_empty_for_the_caller_to_fall_back():
    assert normalize_org_slug("!!!") == ""
    assert normalize_org_slug("a") == ""  # under the 2-character minimum


@pytest.mark.django_db
def test_collision_suffix_stays_inside_the_cap():
    first = unique_org_slug(LONG_NAME)
    Org.objects.create(slug=first, display_name=LONG_NAME)
    second = unique_org_slug(LONG_NAME)
    assert second != first
    assert len(second) <= MAX_SLUG_LENGTH
    assert ADD_TEAM_SLUG.match(second)


@pytest.mark.django_db
def test_onboarding_derives_a_short_slug_from_a_long_name():
    form = OnboardingForm(data={"display_name": LONG_NAME, "unit_name": "slices"})
    assert form.is_valid(), form.errors
    assert ADD_TEAM_SLUG.match(form.cleaned_data["slug"])


@pytest.mark.django_db
def test_onboarding_rejects_a_typed_slug_that_is_too_long():
    typed = "a" * (MAX_SLUG_LENGTH + 1)
    form = OnboardingForm(data={"display_name": "Fine Name", "slug": typed})
    assert not form.is_valid()
    assert "slug" in form.errors
