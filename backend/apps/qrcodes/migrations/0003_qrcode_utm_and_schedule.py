from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qrcodes", "0002_qrcode_outer_shape"),
    ]

    operations = [
        migrations.AddField(
            model_name="qrcode",
            name="utm_params",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="qrcode",
            name="scheduled_active_from",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="qrcode",
            name="scheduled_active_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
