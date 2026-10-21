"""Each holding's typed class name becomes a StockClass of that org, name kept exactly.

Every new class is COMMON with the defaults (votes 1, seniority 1, nothing authorized
recorded); an admin sets the rest on the cap table page. Reversible: going back writes
each holding's class name into the text column again.
"""

from django.db import migrations


def forward(apps, schema_editor):
    StockClass = apps.get_model("captable", "StockClass")
    StockHolding = apps.get_model("captable", "StockHolding")
    made = {}
    for h in StockHolding.objects.all().order_by("id"):
        name = h.stock_class_name or "Common"
        key = (h.org_id, name)
        if key not in made:
            made[key], _ = StockClass.objects.get_or_create(org_id=h.org_id, name=name)
        h.stock_class = made[key]
        h.save(update_fields=["stock_class"])


def backward(apps, schema_editor):
    StockHolding = apps.get_model("captable", "StockHolding")
    for h in StockHolding.objects.select_related("stock_class"):
        h.stock_class_name = h.stock_class.name if h.stock_class_id else "Common"
        h.save(update_fields=["stock_class_name"])


class Migration(migrations.Migration):
    dependencies = [("captable", "0002_carta_parity")]
    operations = [migrations.RunPython(forward, backward)]
