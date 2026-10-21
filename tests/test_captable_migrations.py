"""
captable 0002-0004 turn StockHolding.stock_class from text into a StockClass row.

Pinned down: every holding keeps its class name exactly, one class per distinct name per
org, and going back writes the names into the text column again.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("captable", "0001_initial")]
AFTER = [("captable", "0004_stock_class_required")]


def _migrate(target):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(target)
    return executor.loader.project_state(target).apps


@pytest.mark.django_db(transaction=True)
def test_class_text_becomes_a_class_and_back(org_factory, user_factory, membership_factory):
    org = org_factory(slug="venture")
    other = org_factory(slug="other")
    m = membership_factory(org=org, user=user_factory(email="a@example.com"))
    n = membership_factory(org=other, user=user_factory(email="b@example.com"))

    old = _migrate(BEFORE)
    Holding = old.get_model("captable", "StockHolding")
    Holding.objects.create(org_id=org.pk, membership_id=m.pk, quantity=10, stock_class="Common")
    Holding.objects.create(org_id=org.pk, membership_id=m.pk, quantity=5, stock_class="Common")
    Holding.objects.create(
        org_id=org.pk, membership_id=m.pk, quantity=3, stock_class="Series Seed Preferred"
    )
    Holding.objects.create(org_id=other.pk, membership_id=n.pk, quantity=7, stock_class="Common")

    new = _migrate(AFTER)
    StockClass = new.get_model("captable", "StockClass")
    Holding = new.get_model("captable", "StockHolding")
    assert sorted(StockClass.objects.values_list("org_id", "name")) == sorted(
        [(org.pk, "Common"), (org.pk, "Series Seed Preferred"), (other.pk, "Common")]
    )
    got = sorted(
        (h.org_id, h.quantity, h.stock_class.name, h.stock_class.org_id)
        for h in Holding.objects.select_related("stock_class")
    )
    assert got == sorted(
        [
            (org.pk, 10, "Common", org.pk),
            (org.pk, 5, "Common", org.pk),
            (org.pk, 3, "Series Seed Preferred", org.pk),
            (other.pk, 7, "Common", other.pk),
        ]
    )

    old = _migrate(BEFORE)
    Holding = old.get_model("captable", "StockHolding")
    assert sorted(Holding.objects.values_list("quantity", "stock_class")) == [
        (3, "Series Seed Preferred"),
        (5, "Common"),
        (7, "Common"),
        (10, "Common"),
    ]
    _migrate(AFTER)
