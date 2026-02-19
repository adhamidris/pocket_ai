from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("conversations", "0002_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="IdentifierEvent",
        ),
    ]
