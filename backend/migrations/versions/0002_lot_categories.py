"""Add categories to existing timber lots."""

from alembic import op

revision = "0002_lot_categories"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info('lots')")}
    if "category" not in columns:
        connection.exec_driver_sql(
            "ALTER TABLE lots ADD COLUMN category VARCHAR NOT NULL DEFAULT 'timber'"
        )


def downgrade():
    connection = op.get_bind()
    columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info('lots')")}
    if "category" in columns:
        connection.exec_driver_sql("ALTER TABLE lots DROP COLUMN category")
