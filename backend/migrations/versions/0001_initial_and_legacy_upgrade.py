"""Create application tables and add traceability fields to legacy lots."""

from alembic import op
import sqlalchemy as sa

from backend.database import Base
from backend import models  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)

    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "is_active" not in user_columns:
        op.add_column(
            "users",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    lot_columns = {column["name"] for column in inspector.get_columns("lots")}
    if "category" not in lot_columns:
        op.add_column(
            "lots",
            sa.Column("category", sa.String(), nullable=False, server_default="timber"),
        )
    if "dimensions" not in lot_columns:
        op.add_column("lots", sa.Column("dimensions", sa.String(), nullable=True))
    if "pieces" not in lot_columns:
        op.add_column("lots", sa.Column("pieces", sa.Integer(), nullable=True))
    if "status" not in lot_columns:
        op.add_column(
            "lots",
            sa.Column("status", sa.String(), nullable=False, server_default="in_stock"),
        )
    if "purchase_order_id" not in lot_columns:
        op.add_column("lots", sa.Column("purchase_order_id", sa.Integer(), nullable=True))


def downgrade():
    bind = op.get_bind()
    user_columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "is_active" in user_columns:
        op.drop_column("users", "is_active")
    lot_columns = {column["name"] for column in sa.inspect(bind).get_columns("lots")}
    for column in ("purchase_order_id", "status", "pieces", "dimensions"):
        if column in lot_columns:
            op.drop_column("lots", column)
