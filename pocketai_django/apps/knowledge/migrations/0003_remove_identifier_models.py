from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0002_performance_indexes"),
    ]

    operations = [
        migrations.DeleteModel(
            name="IdentifierColumnMapping",
        ),
        migrations.DeleteModel(
            name="IdentifierColumnMemory",
        ),
        migrations.DeleteModel(
            name="IdentifierSchema",
        ),
    ]
