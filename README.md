# Globant Data Engineering Challenge – PoC

🇬🇧 English · [🇪🇸 Español](README.es.md)

Python 3.12 · FastAPI · PostgreSQL · AVRO · Docker · (AWS + Terraform + GitHub Actions)

Architecture diagram (ES/EN): [https://ulises1211.github.io/globant-data-challenge/](https://ulises1211.github.io/globant-data-challenge/)

## What it does

| Requirement | Where |
|---|---|
| Historical CSV → SQL migration | `app/loader.py` |
| REST ingestion API (1–1000 rows/request, all 3 tables) | `app/main.py` |
| Validation + rejected-record logging | `app/validators.py`, `app/ingestion.py` |
| AVRO backup / restore | `app/backup.py` |
| Analytics endpoints (2021, valid rows only) | `app/analytics.py`, `sql/*.sql` |

## Run locally

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/Mac: source .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d db                              # PostgreSQL on localhost:54329

export DATABASE_URL=postgresql://globant:globant@localhost:54329/globant
python -m app.loader --dir data                      # historical migration
API_KEY=dev-key uvicorn app.main:app --reload        # http://localhost:8000/docs
```

Tests (unit tests always; integration tests need the database):

```bash
pytest
TEST_DATABASE_URL=$DATABASE_URL pytest
```

## API

Send `x-api-key` when `API_KEY` is set. Bodies are JSON arrays of 1–1000 records.

| Method | Path | Purpose |
|---|---|---|
| POST | `/departments`, `/jobs`, `/hired-employees` | Batch ingestion |
| POST | `/backup/{table}` | Full table → AVRO file |
| POST | `/restore/{table}` | Restore from AVRO (`{"key": "...", "mode": "replace\|merge"}`) |
| GET | `/analytics/hires-by-quarter` | Hires per department/job by 2021 quarter |
| GET | `/analytics/departments-above-average` | Departments above the 2021 average |

Ingestion status: `201` all inserted · `207` some rejected · `422` none inserted or invalid batch size.
The response lists every rejected record with its reason.

## Design decisions

- **One endpoint per table** (not a generic one): each table has a different contract (only
  `hired_employees` has a date and foreign keys), so explicit routes give a precise OpenAPI
  document. The logic is shared in one `ingest()` function, so nothing is duplicated.
- **Same validator for CSV, API and restore**, so history and new data follow identical rules.
- **Rules:** all fields required; `datetime` ISO 8601 (stored as `TIMESTAMPTZ`, UTC);
  `department_id` / `job_id` must exist; ids are positive integers and unique.
- **Invalid records are never inserted.** They go to the `rejected_records` table
  (table, source, raw record as JSON, reason) and to the application log. Valid and rejected
  writes share one transaction.
- **Analytics:** the year filter is a half-open UTC range on `datetime` (index friendly).
  Departments with no hires count as 0 in the "average across all departments".
- **Restore** re-validates rows. `replace` deletes then loads in one transaction (fails with `409` if
  other tables still reference the rows); `merge` upserts.
- **Backups** go to `BACKUP_DIR` locally, or to S3 when `BACKUP_S3_BUCKET` is set.
- **PostgreSQL (RDS) instead of a warehouse or lake:** the challenge asks for a SQL database with
  foreign-key validation, small batch inserts and a tiny dataset. See "Big Data evolution" for
  when that would change.

## Data findings (provided CSVs)

- `departments`: 12 rows · `jobs`: 183 rows · `hired_employees`: 1,999 rows (no header row).
- 1,929 valid, **70 rejected** (all with a missing field: 21 `department_id`, 19 `name`,
  16 `job_id`, 14 `datetime`). 1,643 valid hires fall in 2021; the rest are 2022.

## Security

- API key on every route (`x-api-key`, constant-time comparison). On AWS, API Gateway enforces
  its own key and usage plan.
- Parameterised SQL only; table names come from a fixed allow-list.
- Backup keys are checked against path traversal.

## AWS deployment (us-east-1)

Everything is created by Terraform (`infra/`) and deployed by GitHub Actions. Nothing is
applied from a laptop, and no access keys are stored anywhere (GitHub assumes an AWS role
through OIDC).

| Piece | Terraform file |
|---|---|
| Private VPC, endpoints for S3 and Secrets Manager (no NAT) | `network.tf` |
| RDS PostgreSQL, password generated and stored by AWS in Secrets Manager | `rds.tf` |
| S3 buckets: raw CSVs and versioned AVRO backups | `s3.tf` |
| ECR repository | `ecr.tf` |
| Two Lambdas (API and loader) from one container image | `lambda.tf`, `iam.tf` |
| API Gateway REST API with API key and usage plan | `apigw.tf` |

**One-time setup** (AWS console, about 10 minutes)

1. Secure the account: MFA on the root user and a monthly budget alert.
2. CloudFormation → create a stack from `infra/bootstrap/github-oidc.yaml` in `us-east-1`.
   It creates the GitHub OIDC trust, the role GitHub Actions assumes and the Terraform state bucket.
3. GitHub repository → Settings → Variables: `AWS_ROLE_ARN`, `TF_STATE_BUCKET`, `AWS_REGION`
   (values come from the stack outputs).
4. GitHub repository → Settings → Environments: create `production` with yourself as required reviewer.

**Workflows** (`.github/workflows/`)

| Workflow | Trigger | What it does |
|---|---|---|
| `ci.yml` | Pull request | Tests, `terraform fmt` / `validate` / `plan` |
| `deploy.yml` | Merge to `main` | Tests, build image, push to ECR, `terraform apply` (needs approval) |
| `load-data.yml` | Manual | Runs the loader Lambda: CSVs from S3 into PostgreSQL. The `reset` option empties all tables first (test rows included) and reloads from scratch |
| `destroy.yml` | Manual | Removes everything Terraform created |

**How a code change reaches AWS**

| Action | What happens |
|---|---|
| `git push` to a feature branch, or opening a pull request | CI only: tests and `terraform plan`. **Nothing changes in AWS.** |
| Merge to `main` | `deploy.yml` starts and waits for your approval in `production`. |
| Approve the deployment | A new image is built and pushed to ECR with a unique tag (`commit-run number`). Terraform sees the new `image_uri` and updates both Lambdas, which share the image. |

After the first deployment a code change takes about 3 to 5 minutes. Recommended flow:
branch → push → pull request → CI is green → merge → approve. Pushing straight to `main` also
deploys, but skips the chance to review the plan. To roll back, `git revert` the commit on `main`
and approve the deployment. `deploy.yml` can also be started by hand from Actions → Run workflow.

**First use:** run `deploy.yml`, then `load-data.yml`. In the API Gateway console, open
*API keys* and reveal the key; call `https://<api-id>.execute-api.us-east-1.amazonaws.com/v1/...`
with the header `x-api-key`. (Swagger at `/docs` is not usable through API Gateway; use the
endpoint table above.)

**Cost:** RDS `db.t4g.micro` fits the free tier. There is no NAT Gateway; the Secrets Manager
endpoint costs about 7 USD per month. Run `destroy.yml` when you finish.

The IAM role used by GitHub Actions has `AdministratorAccess` because Terraform creates IAM roles,
a VPC and more. Its trust is limited to this single repository; narrow the policy before any real use.

## Big Data evolution

This PoC already follows a lightweight medallion pattern:

| Layer | In this PoC | At scale |
|---|---|---|
| Bronze (raw) | CSV files in `S3 · raw`, plus the original JSON of every rejected row in `rejected_records` | Append-only raw Iceberg tables on S3, fed by CDC or streams |
| Silver (clean) | Validated tables `departments`, `jobs`, `hired_employees` | Cleaned, conformed Iceberg tables; validation as Spark/Glue jobs with data-quality checks |
| Gold (business) | The two analytics endpoints (SQL) | Aggregated Iceberg tables or views served to BI |

**Target architecture if volume or consumers grow:**

```
RDS PostgreSQL ──CDC (DMS)──► Bronze ──validate/clean──► Silver ──aggregate──► Gold ──► Athena · Snowflake · BI
 (system of record)          Iceberg on S3 (Glue Data Catalog, Lake Formation for governance)
```

- **Keep PostgreSQL as the system of record** for ingestion and constraints. Add the lake
  downstream, fed by change data capture (AWS DMS), instead of replacing the database.
- **Iceberg on S3** gives ACID tables, schema evolution and time travel. Snapshots would replace
  the AVRO exports as the backup and restore mechanism.
- **Snowflake or Redshift** as analytical warehouse when many BI users need concurrent queries.
- **Streaming ingestion** (Kinesis or MSK) instead of REST batches if events arrive continuously.
- **Orchestration and quality:** Step Functions or MWAA for pipelines, Deequ or Great
  Expectations for data-quality rules.

**When to make the move:** tens of millions of rows or more, several source systems, or many
analytical consumers. **Why not now:** the challenge is a small transactional workload that needs
enforced foreign keys, and it explicitly asks to prioritise clarity over over-engineering.
Snowflake does not enforce foreign keys, so validation would stay in the application anyway.
