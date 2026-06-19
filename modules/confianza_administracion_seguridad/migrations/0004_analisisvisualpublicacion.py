from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('confianza_administracion_seguridad', '0003_publicationreport'),
        ('gestion_cocinero', '0005_merge_20260617_0156'),
    ]

    operations = [
        migrations.CreateModel(
            name='AnalisisVisualPublicacion',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('publication', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='analisis_visual',
                    to='gestion_cocinero.dish',
                    verbose_name='Publicación'
                )),
                ('estado', models.CharField(
                    blank=True, null=True, max_length=20,
                    choices=[
                        ('VALIDO', 'Válido'),
                        ('SOSPECHOSO', 'Sospechoso'),
                        ('RECHAZADO', 'Rechazado'),
                        ('ERROR', 'Error'),
                    ]
                )),
                ('riesgo', models.IntegerField(blank=True, null=True)),
                ('nivel_riesgo', models.CharField(
                    blank=True, null=True, max_length=10,
                    choices=[
                        ('BAJO', 'Bajo'),
                        ('MEDIO', 'Medio'),
                        ('ALTO', 'Alto'),
                    ]
                )),
                ('es_comida', models.BooleanField(blank=True, null=True)),
                ('coincide_con_plato', models.BooleanField(blank=True, null=True)),
                ('coincidencia', models.IntegerField(blank=True, null=True)),
                ('parece_generada_por_ia', models.BooleanField(blank=True, null=True)),
                ('probabilidad_ia', models.IntegerField(blank=True, null=True)),
                ('imagen_generica_o_stock', models.BooleanField(blank=True, null=True)),
                ('imagen_borrosa_o_baja_calidad', models.BooleanField(blank=True, null=True)),
                ('contenido_no_apto', models.BooleanField(blank=True, null=True)),
                ('objetos_detectados', models.JSONField(blank=True, default=list)),
                ('motivos', models.JSONField(blank=True, default=list)),
                ('motivos_ia', models.JSONField(blank=True, default=list)),
                ('recomendacion', models.TextField(blank=True)),
                ('accion_sugerida', models.CharField(
                    blank=True, null=True, max_length=10,
                    choices=[
                        ('APROBAR', 'Aprobar'),
                        ('REVISAR', 'Revisar'),
                        ('OCULTAR', 'Ocultar'),
                        ('RECHAZAR', 'Rechazar'),
                    ]
                )),
                ('proveedor_vision', models.CharField(blank=True, default='GROQ_VISION', max_length=50)),
                ('proveedor_deteccion_ia', models.CharField(blank=True, default='NO_CONFIGURADO', max_length=50)),
                ('error_controlado', models.BooleanField(default=False)),
                ('detalle_error', models.TextField(blank=True, null=True)),
                ('analizado_en', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'analisis_visual_publicacion',
                'verbose_name': 'Análisis Visual de Publicación',
                'verbose_name_plural': 'Análisis Visuales de Publicaciones',
            },
        ),
    ]
