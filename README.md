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

## How to test the API

The same commands work against your local API (`http://localhost:8000`, key `dev-key`) and the
deployed one on AWS. For AWS you need two values:

- **`<API_URL>`**: GitHub → Actions → last **Deploy** → step *Show outputs* → `api_url`
  (or API Gateway → *Stages* → `v1` → *Invoke URL*). It ends in `/v1`.
- **`<API_KEY>`**: API Gateway → *API keys* → `globant-poc-client` → *Show*.
  Never commit the real key; the examples use placeholders.

The first call after a pause can take a few seconds (Lambda and database cold start).

| Check | Expected result |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| `GET /analytics/departments-above-average` | 7 departments: Support 216, Engineering 205, Human Resources 201, Services 200, Business Development 185, Research and Development 148, Marketing 142 |
| `GET /analytics/hires-by-quarter` | 933 rows, 1,643 hires in total |
| `POST /hired-employees` with `"datetime":"2021-01-01"` | HTTP 422, `rejected: 1`, reason *"datetime is not ISO 8601"*; nothing is inserted |
| `POST /backup/departments` | `rows: 12` and the path of the new `.avro` file (`s3://...` on AWS) |

**Windows PowerShell**

```powershell
$api = "<API_URL>"
$key = "<API_KEY>"
$h   = @{ "x-api-key" = $key }

Invoke-RestMethod "$api/health" -Headers $h
Invoke-RestMethod "$api/analytics/departments-above-average" -Headers $h | Format-Table
(Invoke-RestMethod "$api/analytics/hires-by-quarter" -Headers $h).Count      # 933

# Invalid record (bad date): must be rejected. Body goes in a file to avoid quoting problems.
'[{"id":99999,"name":"Test","datetime":"2021-01-01","department_id":1,"job_id":1}]' | Set-Content body.json -Encoding ascii
curl.exe -s -X POST "$api/hired-employees" -H "x-api-key: $key" -H "content-type: application/json" --data "@body.json"

# AVRO backup
curl.exe -s -X POST "$api/backup/departments" -H "x-api-key: $key"
```

**macOS and Linux (Terminal, bash or zsh)**

```bash
API="<API_URL>"
KEY="<API_KEY>"

curl -s -H "x-api-key: $KEY" "$API/health"
curl -s -H "x-api-key: $KEY" "$API/analytics/departments-above-average"       # add | jq for pretty output
curl -s -H "x-api-key: $KEY" "$API/analytics/hires-by-quarter" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"   # 933

# Invalid record (bad date): must be rejected
curl -s -X POST "$API/hired-employees" -H "x-api-key: $KEY" -H "content-type: application/json" \
     -d '[{"id":99999,"name":"Test","datetime":"2021-01-01","department_id":1,"job_id":1}]'

# AVRO backup
curl -s -X POST "$API/backup/departments" -H "x-api-key: $KEY"
```

Do not insert a valid test employee unless you plan to clean it up: it changes the analytics counts.
If it happens, run **Load historical data** with `reset` checked to start again from the CSV files.
(iPhone and iPad have no terminal; use a REST client app with the same URLs and the `x-api-key` header.)

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
- **Endpoints are `def`, not `async def`, on purpose.** The database driver (psycopg) and boto3
  are blocking libraries. FastAPI runs a plain `def` endpoint in a thread pool, so the event loop
  never freezes. An `async def` endpoint calling a blocking library would stall the whole server
  while it waits. `async def` pays off only with async drivers and many concurrent requests per
  process, and a Lambda container serves one request at a time.
- **Code conventions:** imports at the top of every module (standard library, third party, local),
  and every module, class, function and method has a bilingual (English / Spanish) docstring.

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

## Tear down (stop all charges)

Do it in this order, because the destroy workflow needs the role and the state bucket that step 3
removes.

1. GitHub → Actions → **Destroy** → *Run workflow*, type `destroy`, approve in `production`.
   It removes RDS, the VPC and endpoints, both Lambdas, API Gateway, ECR and the two data buckets.
   If it fails with a `DependencyViolation` on a subnet or security group, wait 10 to 20 minutes
   (Lambda network interfaces are released late) and run it again.
2. In the AWS console, check that nothing is left: RDS, VPC (endpoints), Lambda, API Gateway, ECR,
   S3 and Secrets Manager (region us-east-1).
3. CloudFormation → delete the stack `globant-poc-bootstrap` (removes the GitHub role and OIDC trust).
4. S3 → bucket `globant-poc-tfstate-<account id>` → **Empty**, then **Delete** (the stack keeps it on purpose).
5. Next day, Billing → Cost Explorer should show no new charges. The budget alert is free to keep.

To bring everything back: run **Deploy**, then **Load historical data** with `reset` checked.
(After step 3 you must repeat the one-time setup first.)

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
