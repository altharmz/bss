import os
import time
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    # Keep loggers that exist already: the test suite migrates in-process, and the default
    # (True) silenced every app.* logger for the rest of the session.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


def get_url() -> str:
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


# Rollback drill only (docs/operations.md "Rollback"): scripts/deploy.sh passes it to the
# one-off migration container under BSS_DEPLOY_DRILL=fail-migration|slow-migration or
# BSS_ROLLBACK_DRILL=slow-downgrade. It acts after the migrations ran but inside their one
# transaction, so the live drill proves that a failed or overrunning upgrade or downgrade
# leaves the schema as it was. `slow` outlives deploy.sh's deadline, which removes the
# container; the raise after the sleep is only a backstop.
DRILL_SLEEP_SECONDS = 600


def migration_drill() -> None:
    mode = os.environ.get("BSS_MIGRATION_DRILL", "")
    if not mode:
        return
    if mode == "fail":
        raise RuntimeError("drill: forced migration failure (BSS_MIGRATION_DRILL=fail)")
    if mode == "slow":
        time.sleep(DRILL_SLEEP_SECONDS)
        raise RuntimeError("drill: the migration overran on purpose (BSS_MIGRATION_DRILL=slow)")
    raise RuntimeError(f"unknown BSS_MIGRATION_DRILL={mode} (fail or slow)")


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
            migration_drill()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
