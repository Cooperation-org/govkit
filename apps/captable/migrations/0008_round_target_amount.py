from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive: the round size a company is asking for."""

    dependencies = [("captable", "0007_stage_pool_dilution")]

    operations = [
        migrations.AddField(
            model_name="round",
            name="target_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True),
        ),
    ]
