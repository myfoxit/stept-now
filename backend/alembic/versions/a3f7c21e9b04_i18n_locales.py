"""i18n: locale columns and per-locale help-center articles

Adds the language a row is served in:

- `users.locale` / `contacts.locale` — nullable, because "no stored preference,
  follow the browser" is a real state we must be able to represent.
- `articles.locale` / `article_collections.locale` — NOT NULL, defaulted to
  `en` for existing rows, since every article already written is in *some*
  language and pretending otherwise would break slug uniqueness.

Article slugs become unique per (workspace, locale) rather than per workspace,
so `/de/passwort-zuruecksetzen` and `/en/reset-password` coexist. `translation_key`
groups the variants of one article across languages; it backfills to the row's
own id, so every existing article becomes a translation group of one and no
existing URL changes.

Revision ID: a3f7c21e9b04
Revises: b7c04e1aa93d
Create Date: 2026-08-11

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3f7c21e9b04"
down_revision = "b7c04e1aa93d"
branch_labels = None
depends_on = None

#: (table, old unique constraint, new slug constraint, new translation constraint)
_ARTICLE_TABLES = (
    (
        "articles",
        "uq_articles_ws_slug",
        "uq_articles_ws_locale_slug",
        "uq_articles_ws_translation_locale",
    ),
    (
        "article_collections",
        "uq_article_collections_ws_slug",
        "uq_article_collections_ws_locale_slug",
        None,
    ),
)


def upgrade() -> None:
    op.add_column("users", sa.Column("locale", sa.String(length=12), nullable=True))
    op.add_column("contacts", sa.Column("locale", sa.String(length=12), nullable=True))

    for table, old_uq, slug_uq, translation_uq in _ARTICLE_TABLES:
        # server_default backfills existing rows in one statement; it is dropped
        # immediately after so the DB matches the model (which defaults in
        # Python) and `alembic check` stays quiet.
        op.add_column(
            table,
            sa.Column("locale", sa.String(length=12), nullable=False, server_default="en"),
        )
        op.alter_column(table, "locale", server_default=None)
        op.add_column(table, sa.Column("translation_key", sa.String(length=40), nullable=True))
        op.execute(f"UPDATE {table} SET translation_key = id")  # noqa: S608 — table is a literal
        op.alter_column(table, "translation_key", nullable=False)

        op.create_index(f"ix_{table}_locale", table, ["locale"])
        op.create_index(f"ix_{table}_translation_key", table, ["translation_key"])

        op.drop_constraint(old_uq, table, type_="unique")
        op.create_unique_constraint(slug_uq, table, ["workspace_id", "locale", "slug"])
        if translation_uq:
            op.create_unique_constraint(
                translation_uq, table, ["workspace_id", "translation_key", "locale"]
            )


def downgrade() -> None:
    for table, old_uq, slug_uq, translation_uq in _ARTICLE_TABLES:
        if translation_uq:
            op.drop_constraint(translation_uq, table, type_="unique")
        op.drop_constraint(slug_uq, table, type_="unique")
        # Going back to a workspace-wide unique slug cannot succeed while two
        # locales share one; keep the first row per slug and drop the rest,
        # which is the only reversal that can complete without inventing slugs.
        op.execute(  # noqa: S608 — table is a literal
            f"DELETE FROM {table} WHERE id NOT IN "
            f"(SELECT MIN(id) FROM {table} GROUP BY workspace_id, slug)"
        )
        op.create_unique_constraint(old_uq, table, ["workspace_id", "slug"])

        op.drop_index(f"ix_{table}_translation_key", table_name=table)
        op.drop_index(f"ix_{table}_locale", table_name=table)
        op.drop_column(table, "translation_key")
        op.drop_column(table, "locale")

    op.drop_column("contacts", "locale")
    op.drop_column("users", "locale")
