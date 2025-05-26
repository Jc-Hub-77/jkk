# Celery Configuration

# Broker URL - Redis
broker_url = "redis://localhost:6379/0"

# Result Backend - Redis
result_backend = "redis://localhost:6379/0"

# Other Celery settings (optional for now)
task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "UTC"
enable_utc = True
