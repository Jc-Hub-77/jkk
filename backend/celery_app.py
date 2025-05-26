from celery import Celery

# Create a Celery application instance
app = Celery("backend_app")

# Load Celery configuration from celery_config.py
app.config_from_object("backend.celery_config")

# Auto-discover tasks from all installed apps (assuming tasks are defined in files like tasks.py)
app.autodiscover_tasks(["backend.tasks", "backend.tasks.live_trading_tasks", "backend.tasks.backtesting_tasks"])

if __name__ == "__main__":
    app.start()
