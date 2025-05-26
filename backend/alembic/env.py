from logging.config import fileConfig
import os
import sys

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Import settings from your application's config to ensure consistent DB URL
from backend.config import settings as app_settings # Renamed to avoid conflict with alembic's 'settings'

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

# Ensure the project root is in sys.path to find backend.models
# The prepend_sys_path = . in alembic.ini should handle this if alembic is run from root.

# Explicitly import all models to ensure they are registered with Base.metadata
from backend.models import (
    Base, 
    User, 
    Profile, 
    ApiKey, 
    Strategy, 
    UserStrategySubscription, 
    Order, 
    Position, 
    BacktestResult, 
    PaymentTransaction, 
    Referral,
    BacktestReport # Ensure this is also imported if it's a distinct model used
)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

def get_url():
    # Use the DATABASE_URL from the application's settings
    return app_settings.DATABASE_URL

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url() # Use the URL from app_settings
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Create a new section in the config or update the existing one
    # This ensures engine_from_config uses the correct URL from app_settings
    db_config = config.get_section(config.config_ini_section, {})
    db_config["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        db_config, # Use the modified config section
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
