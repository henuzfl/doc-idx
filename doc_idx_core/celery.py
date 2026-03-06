import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'doc_idx_core.settings')

app = Celery('doc_idx')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Celery configuration
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_url='redis://localhost:6379/0',
    result_backend='redis://localhost:6379/0',
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
)
