from django.db import migrations, models


class Migration(migrations.Migration):
    """Additive: a new ``stage`` column. ``pool_dilution`` is the model's new name for the
    existing column, which keeps its name (no table change)."""

    dependencies = [("captable", "0006_company_document")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
        migrations.RenameField(
            model_name="captablesettings",
            old_name="pool_dilutes_at_priced_round",
            new_name="pool_dilution",
        ),
        migrations.AlterField(
            model_name="captablesettings",
            name="pool_dilution",
            field=models.BooleanField(default=False, db_column="pool_dilutes_at_priced_round"),
        ),
            ],
        ),
        migrations.AddField(
            model_name="captablesettings",
            name="stage",
            field=models.CharField(
                choices=[
                    ("PRE_SEED", "Pre-seed"),
                    ("SEED", "Seed"),
                    ("SERIES_A", "Series A"),
                    ("LATER", "Later"),
                ],
                default="PRE_SEED",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="captablesettings",
            name="scope",
            field=models.CharField(
                choices=[("WHOLE_COMPANY", "Whole company"), ("POOL_ONLY", "Founders pool only")],
                default="WHOLE_COMPANY",
                max_length=16,
            ),
        ),
    ]
