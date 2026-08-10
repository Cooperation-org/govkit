from django.db import migrations, models


def publish_what_already_went(apps, schema_editor):
    """A page that was live because its email had been sent stays live.

    Publishing used to be something only sending did, and the public page was
    gated on sent_at. Splitting the two must not silently 404 a link somebody
    has already shared.
    """
    Send = apps.get_model("comms", "Send")
    Send.objects.filter(sent_at__isnull=False).update(published_at=models.F("sent_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0002_subscriber"),
    ]

    operations = [
        migrations.AddField(
            model_name="send",
            name="published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(publish_what_already_went, migrations.RunPython.noop),
    ]
