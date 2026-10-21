import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("captable", "0003_stock_class_from_text")]

    operations = [
        migrations.RemoveField(model_name="stockholding", name="stock_class_name"),
        migrations.AlterField(
            model_name="stockholding",
            name="stock_class",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="holdings",
                to="captable.stockclass",
            ),
        ),
        migrations.AlterModelOptions(
            name="stockholding",
            options={"ordering": ["stock_class__name", "-quantity", "id"]},
        ),
    ]
