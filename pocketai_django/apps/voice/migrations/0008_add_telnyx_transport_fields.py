from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("voice", "0007_voiceproviderconnection"),
    ]

    operations = [
        migrations.AddField(
            model_name="callsession",
            name="provider_call_sid",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="callsession",
            name="provider_stream_sid",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="callsession",
            name="transport_provider",
            field=models.CharField(blank=True, db_index=True, default="", max_length=24),
        ),
        migrations.AddField(
            model_name="voiceconfiguration",
            name="active_transport_provider",
            field=models.CharField(
                blank=True,
                choices=[("twilio", "Twilio"), ("telnyx", "Telnyx")],
                default="",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="voicephonenumber",
            name="provider",
            field=models.CharField(
                choices=[("twilio", "Twilio"), ("telnyx", "Telnyx")],
                default="twilio",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="voiceproviderconnection",
            name="provider",
            field=models.CharField(
                choices=[
                    ("twilio", "Twilio"),
                    ("telnyx", "Telnyx"),
                    ("deepgram", "Deepgram"),
                    ("elevenlabs", "ElevenLabs"),
                ],
                max_length=24,
            ),
        ),
    ]
