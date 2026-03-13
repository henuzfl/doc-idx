import os
from pathlib import Path
from dotenv import load_dotenv
from celery import Celery

# 加载 .env 文件
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'doc_idx_core.settings')

app = Celery('doc_idx')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# 从环境变量读取 Redis 配置
redis_host = os.getenv('REDIS_HOST', 'localhost')
redis_port = os.getenv('REDIS_PORT', '6379')
redis_db = os.getenv('REDIS_DB', '0')
redis_password = os.getenv('REDIS_PASSWORD', '')

if redis_password:
    broker_url = f'redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}'
    result_backend = f'redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}'
else:
    broker_url = f'redis://{redis_host}:{redis_port}/{redis_db}'
    result_backend = f'redis://{redis_host}:{redis_port}/{redis_db}'

# Celery configuration - 支持优先级队列
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    broker_url=broker_url,
    result_backend=result_backend,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes

    # 优先级队列配置
    task_routes={
        'rag_app.tasks.process_document_high': {'queue': 'high'},
        'rag_app.tasks.process_document': {'queue': 'default'},
    },
    # 队列顺序（高优先级在前）
    task_default_queue='default',
    task_queues={
        'high': {},
        'default': {},
        'low': {},
    },
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
)
