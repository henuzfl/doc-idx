# 文档问答系统 (Document Q&A System)

基于 Django + LlamaIndex 的企业级文档问答系统，支持多租户、向量检索、S3 对象存储。

## 功能特性

- **多租户支持**: 完全隔离的租户数据管理
- **文档管理**: 支持 PDF、TXT、DOC 等格式上传
- **向量检索**: 基于 pgVector 的语义搜索
- **RAG 对话**: 基于文档内容的智能问答
- **历史记录**: 会话管理和对话历史
- **S3 存储**: 支持 MinIO/S3 对象存储
- **分页查询**: 文档列表分页展示

## 技术栈

- **后端**: Django 4.2 + Django REST Framework
- **向量数据库**: PostgreSQL + pgVector
- **LLM/Embedding**: 阿里云 DashScope (通义千问)
- **前端**: HTML + JavaScript (ChatGPT 风格)
- **对象存储**: MinIO / S3
- **任务队列**: Celery + Redis

## 项目结构

```
doc-idx/
├── doc_idx_core/          # Django 项目配置
│   ├── settings.py        # 项目设置
│   ├── urls.py           # URL 路由
│   └── celery.py         # Celery 配置
├── rag_app/              # 主应用
│   ├── models.py         # 数据模型
│   ├── api/              # API 接口
│   │   ├── views.py      # ViewSets
│   │   ├── serializers.py # 序列化器
│   │   ├── urls.py       # API 路由
│   │   └── related_views.py # 相关文档接口
│   └── services/         # 业务服务
│       ├── llama_service.py   # LlamaIndex 服务
│       ├── s3_service.py      # S3 对象存储
│       └── celery_tasks.py   # 异步任务
├── templates/
│   └── index.html        # 前端页面
└── requirements.txt      # 依赖
```

## 快速开始

### 1. 环境要求

- Python 3.10+
- PostgreSQL 15+ (带 pgVector 扩展)
- Redis (用于 Celery)

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

创建 `.env` 文件：

```env
# 数据库配置
DB_NAME=doc_idx
DB_USER=postgres
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=5432

# 向量数据库配置
VECTOR_DB_NAME=doc_idx_vector
VECTOR_DB_USER=postgres
VECTOR_DB_PASSWORD=your_password
VECTOR_DB_HOST=localhost
VECTOR_DB_PORT=5433

# DashScope API
DASHSCOPE_API_KEY=your_api_key

# S3 配置 (可选)
S3_BUCKET_NAME=doc-idx
S3_ACCESS_KEY=your_access_key
S3_SECRET_KEY=your_secret_key
S3_ENDPOINT_URL=http://localhost:9000
```

### 4. 初始化数据库

```bash
# 创建数据库
createdb doc_idx
createdb doc_idx_vector

# 在向量数据库中启用 pgVector
psql -d doc_idx_vector -c "CREATE EXTENSION IF NOT EXISTS vector;"

# 执行迁移
python manage.py migrate
```

### 5. 启动服务

```bash
python manage.py runserver 0.0.0.0:8000
```

访问 http://localhost:8000

## API 接口

### 租户管理

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | /api/tenants/ | 获取租户列表 |
| POST | /api/tenants/ | 创建租户 |
| DELETE | /api/tenants/{id}/ | 删除租户 |

### 文档管理

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | /api/documents/ | 获取文档列表 (支持分页) |
| POST | /api/documents/ | 上传文档 |
| DELETE | /api/documents/{id}/ | 删除文档 |

**分页参数**:
- `page`: 页码 (默认 1)
- `page_size`: 每页数量 (默认 10)

### 对话

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | /api/chat/ | 获取会话列表 |
| POST | /api/chat/ask/ | 提问 |

**提问请求体**:
```json
{
    "tenant_id": "tenant_001",
    "query": "问题内容",
    "session_id": "可选的会话ID"
}
```

### 相关文档

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | /api/related/search/ | 搜索相关文档 |
| GET | /api/related/by_document/ | 获取指定文档内容 |

## 使用说明

1. **创建租户**: 进入租户管理页面，添加租户
2. **上传文档**: 在知识库页面选择租户，上传文档
3. **等待向量化**: 文档自动进行向量化处理
4. **开始对话**: 选择租户，提问关于文档的问题

## 注意事项

- 确保向量数据库已安装 pgVector 扩展
- 首次上传文档需要等待向量化完成
- S3 配置为可选，不配置时使用本地存储

## 许可证

MIT License
