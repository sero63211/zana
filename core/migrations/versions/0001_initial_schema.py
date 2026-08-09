"""Create the ZANA durable entity schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtimes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "models",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("runtime_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=True),
        sa.Column("family", sa.Text(), nullable=True),
        sa.Column("format", sa.Text(), nullable=True),
        sa.Column("quantization", sa.Text(), nullable=True),
        sa.Column("parameter_count", sa.BigInteger(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("identity_strength", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["runtime_id"], ["runtimes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_models_runtime_id", "models", ["runtime_id"])
    op.create_table(
        "capabilities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("working_dir", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "capability_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("capability_id", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["capability_id"], ["capabilities.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_capability_sources_capability_id", "capability_sources", ["capability_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress_0_1", sa.Float(), nullable=False, server_default="0"),
        sa.Column("phase", sa.Text(), nullable=False, server_default=""),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_json", sa.Text(), nullable=True),
    )
    op.create_table(
        "job_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False, server_default=""),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("progress_0_1", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])
    op.create_index("ix_job_events_created_at", "job_events", ["created_at"])
    op.create_table(
        "build_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("capability_id", sa.Integer(), nullable=False),
        sa.Column("model_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("policy_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("plan_json", sa.Text(), nullable=True),
        sa.Column("hardware_profile_json", sa.Text(), nullable=True),
        sa.Column("baseline_report_digest", sa.Text(), nullable=True),
        sa.Column("candidate_report_digest", sa.Text(), nullable=True),
        sa.Column("image_digest", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_json", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["capability_id"], ["capabilities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["model_key"], ["models.key"], ondelete="RESTRICT"),
    )
    op.create_index("ix_build_jobs_capability_id", "build_jobs", ["capability_id"])
    op.create_index("ix_build_jobs_model_key", "build_jobs", ["model_key"])
    op.create_index("ix_build_jobs_status", "build_jobs", ["status"])
    op.create_table(
        "artifacts",
        sa.Column("digest", sa.Text(), primary_key=True),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reference_count", sa.Integer(), nullable=True),
    )
    op.create_table(
        "images",
        sa.Column("digest", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("config_digest", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("base_model_key", sa.Text(), nullable=False),
        sa.Column("base_model_digest", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "image_artifacts",
        sa.Column("image_digest", sa.Text(), nullable=False),
        sa.Column("artifact_digest", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["image_digest"], ["images.digest"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_digest"], ["artifacts.digest"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("image_digest", "artifact_digest", "role"),
    )
    op.create_table(
        "instances",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("image_digest", sa.Text(), nullable=False),
        sa.Column("runtime_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("state_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["image_digest"], ["images.digest"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["runtime_id"], ["runtimes.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_instances_image_digest", "instances", ["image_digest"])
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_conversations_instance_id", "conversations", ["instance_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("provenance_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_memories_instance_id", "memories", ["instance_id"])
    op.create_table(
        "state_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("instance_id", sa.Integer(), nullable=False),
        sa.Column("image_digest", sa.Text(), nullable=False),
        sa.Column("state_digest", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["instances.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_state_snapshots_instance_id", "state_snapshots", ["instance_id"])


def downgrade() -> None:
    for table in (
        "state_snapshots",
        "memories",
        "messages",
        "conversations",
        "instances",
        "image_artifacts",
        "images",
        "artifacts",
        "build_jobs",
        "job_events",
        "jobs",
        "capability_sources",
        "capabilities",
        "models",
        "runtimes",
    ):
        op.drop_table(table)
