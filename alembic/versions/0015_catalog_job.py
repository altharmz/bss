"""TMF620 import and export jobs.

One table for both resources (`kind` keeps them apart; each has its own path). Jobs run
synchronously inside their request, so a stored job is always finished: Succeeded or
Failed. `url` holds the document as a data: URL - the server never fetches or writes a
remote location. Nothing refers to a job; deleting one never undoes an import.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TYPE catalog_job_kind AS ENUM ('ImportJob', 'ExportJob');
        CREATE TYPE catalog_job_status AS ENUM ('Succeeded', 'Failed');
        CREATE TABLE catalog_job (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at timestamptz NOT NULL DEFAULT now(),
            kind catalog_job_kind NOT NULL,
            status catalog_job_status NOT NULL,
            content_type text NOT NULL,
            path text NOT NULL,
            query text,
            url text,
            error_log text,
            creation_date timestamptz NOT NULL,
            completion_date timestamptz NOT NULL,
            CONSTRAINT catalog_job_query_on_export CHECK (kind = 'ExportJob' OR query IS NULL),
            CONSTRAINT catalog_job_failed_has_log
                CHECK ((status = 'Failed') = (error_log IS NOT NULL)),
            CONSTRAINT catalog_job_completed_after_created
                CHECK (completion_date >= creation_date)
        );
        CREATE INDEX catalog_job_kind_idx ON catalog_job (kind, created_at);
        COMMENT ON TABLE catalog_job IS
            'TMF620 importJob/exportJob: synchronous, finished on insert; documents as data: URLs.';
        COMMENT ON TYPE catalog_job_kind IS
            'Which TMF620 resource a catalog_job row is; ours, equal to its @type.';
        COMMENT ON TYPE catalog_job_status IS
            'The finished values of TMF620 JobStateType; jobs never wait or run in the background.';
    """)
    for column, meaning in {
        "id": "Server-generated identifier.",
        "created_at": "Database insertion time; stable list ordering.",
        "kind": "ImportJob or ExportJob; fixed, decides the resource path.",
        "status": "Succeeded or Failed (JobStateType); final when stored.",
        "content_type": "Format of the document: application/json only.",
        "path": "Root resource the job applies to: the TMF620 base path.",
        "query": "Export scope: NULL for the whole catalog or productCatalog.id=<id>.",
        "url": "The document as a data: URL (import: as received; export: as produced).",
        "error_log": "Why the job failed, as text; set exactly when it failed.",
        "creation_date": "When the job was requested (application clock).",
        "completion_date": "When it finished; the same request, so equal or later.",
    }.items():
        op.execute(f"COMMENT ON COLUMN catalog_job.{column} IS '{meaning}'")


def downgrade() -> None:
    op.execute("""
        DROP TABLE catalog_job;
        DROP TYPE catalog_job_status;
        DROP TYPE catalog_job_kind;
    """)
