# Reto Globant de Ingeniería de Datos – PoC

[🇬🇧 English](README.md) · 🇪🇸 Español

Python 3.12 · FastAPI · PostgreSQL · AVRO · Docker · (AWS + Terraform + GitHub Actions)

Diagrama de arquitectura (ES/EN): [https://ulises1211.github.io/globant-data-challenge/](https://ulises1211.github.io/globant-data-challenge/)

## Qué hace

| Requisito | Dónde |
|---|---|
| Migración histórica CSV → SQL | `app/loader.py` |
| API REST de ingesta (1–1000 filas por request, las 3 tablas) | `app/main.py` |
| Validación y log de registros rechazados | `app/validators.py`, `app/ingestion.py` |
| Backup / restore en AVRO | `app/backup.py` |
| Endpoints de análisis (2021, solo filas válidas) | `app/analytics.py`, `sql/*.sql` |

## Ejecutar en local

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/Mac: source .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d db                              # PostgreSQL en localhost:54329

export DATABASE_URL=postgresql://globant:globant@localhost:54329/globant
python -m app.loader --dir data                      # migración histórica
API_KEY=dev-key uvicorn app.main:app --reload        # http://localhost:8000/docs
```

Pruebas (las unitarias siempre; las de integración necesitan la base de datos):

```bash
pytest
TEST_DATABASE_URL=$DATABASE_URL pytest
```

## API

Envía `x-api-key` cuando `API_KEY` está definida. Los cuerpos son arreglos JSON de 1 a 1000 registros.

| Método | Ruta | Propósito |
|---|---|---|
| POST | `/departments`, `/jobs`, `/hired-employees` | Ingesta por lotes |
| POST | `/backup/{table}` | Tabla completa → archivo AVRO |
| POST | `/restore/{table}` | Restaurar desde AVRO (`{"key": "...", "mode": "replace\|merge"}`) |
| GET | `/analytics/hires-by-quarter` | Contrataciones por departamento y puesto, por trimestre de 2021 |
| GET | `/analytics/departments-above-average` | Departamentos sobre el promedio de contratación de 2021 |

Estados de la ingesta: `201` todo insertado · `207` algunos rechazados · `422` nada insertado o tamaño de lote inválido.
La respuesta lista cada registro rechazado con su motivo.

## Decisiones de diseño

- **Un endpoint por tabla** (no uno genérico): cada tabla tiene un contrato distinto (solo
  `hired_employees` tiene fecha y llaves foráneas), así que las rutas explícitas dan un OpenAPI
  preciso. La lógica está compartida en una sola función `ingest()`, sin duplicación.
- **El mismo validador para CSV, API y restore**, de modo que el histórico y los datos nuevos
  siguen reglas idénticas.
- **Reglas:** todos los campos son obligatorios; `datetime` en ISO 8601 (se guarda como
  `TIMESTAMPTZ`, UTC); `department_id` y `job_id` deben existir; los ids son enteros positivos y únicos.
- **Los registros inválidos nunca se insertan.** Van a la tabla `rejected_records` (tabla, origen,
  registro original en JSON, motivo) y al log de la aplicación. Las escrituras válidas y las
  rechazadas comparten una transacción.
- **Análisis:** el filtro de año es un rango UTC semiabierto sobre `datetime` (aprovecha el índice).
  Los departamentos sin contrataciones cuentan como 0 en el "promedio de todos los departamentos".
- **Restore** vuelve a validar las filas. `replace` borra y carga en una sola transacción (falla con
  `409` si otras tablas aún referencian las filas); `merge` inserta o actualiza.
- **Los backups** van a `BACKUP_DIR` en local, o a S3 cuando `BACKUP_S3_BUCKET` está definida.
- **PostgreSQL (RDS) en lugar de un warehouse o data lake:** el reto pide una base SQL con
  validación de llaves foráneas, inserciones pequeñas y un dataset diminuto. Consulta
  "Evolución Big Data" para saber cuándo eso cambiaría.

## Hallazgos de los datos (CSV entregados)

- `departments`: 12 filas · `jobs`: 183 filas · `hired_employees`: 1,999 filas (sin encabezado).
- 1,929 válidas y **70 rechazadas** (todas con un campo vacío: 21 `department_id`, 19 `name`,
  16 `job_id`, 14 `datetime`). 1,643 contrataciones válidas caen en 2021; el resto son de 2022.

## Seguridad

- API key en todas las rutas (`x-api-key`, comparación en tiempo constante). En AWS, API Gateway
  aplica su propia llave y plan de uso.
- Solo SQL parametrizado; los nombres de tabla salen de una lista fija.
- Las llaves de backup se revisan contra path traversal.

## Despliegue en AWS (us-east-1)

Todo lo crea Terraform (`infra/`) y lo despliega GitHub Actions. Nada se aplica desde una laptop
y no se guarda ninguna llave de acceso (GitHub asume un rol de AWS mediante OIDC).

| Pieza | Archivo Terraform |
|---|---|
| VPC privada, endpoints de S3 y Secrets Manager (sin NAT) | `network.tf` |
| RDS PostgreSQL; AWS genera y guarda la contraseña en Secrets Manager | `rds.tf` |
| Buckets S3: CSV crudos y backups AVRO con versionado | `s3.tf` |
| Repositorio ECR | `ecr.tf` |
| Dos Lambdas (API y loader) desde una sola imagen de contenedor | `lambda.tf`, `iam.tf` |
| API Gateway REST con API key y plan de uso | `apigw.tf` |

**Preparación única** (consola de AWS, unos 10 minutos)

1. Proteger la cuenta: MFA en el usuario root y una alerta de presupuesto mensual.
2. CloudFormation → crear un stack desde `infra/bootstrap/github-oidc.yaml` en `us-east-1`.
   Crea la confianza OIDC con GitHub, el rol que asume GitHub Actions y el bucket del estado de Terraform.
3. Repositorio de GitHub → Settings → Variables: `AWS_ROLE_ARN`, `TF_STATE_BUCKET`, `AWS_REGION`
   (los valores salen de las salidas del stack).
4. Repositorio de GitHub → Settings → Environments: crear `production` con tu usuario como aprobador.

**Workflows** (`.github/workflows/`)

| Workflow | Disparador | Qué hace |
|---|---|---|
| `ci.yml` | Pull request | Pruebas, `terraform fmt` / `validate` / `plan` |
| `deploy.yml` | Merge a `main` | Pruebas, construye la imagen, la sube a ECR y `terraform apply` (requiere aprobación) |
| `load-data.yml` | Manual | Ejecuta la Lambda loader: CSV de S3 a PostgreSQL |
| `destroy.yml` | Manual | Elimina todo lo que creó Terraform |

**Cómo llega un cambio de código a AWS**

| Acción | Qué pasa |
|---|---|
| `git push` a una rama o abrir un pull request | Solo el CI: pruebas y `terraform plan`. **No cambia nada en AWS.** |
| Merge a `main` | Arranca `deploy.yml` y espera tu aprobación en `production`. |
| Aprobar el despliegue | Se construye una imagen nueva y se sube a ECR con un tag único (`commit-número de ejecución`). Terraform ve el nuevo `image_uri` y actualiza las dos Lambdas, que comparten imagen. |

Después del primer despliegue, un cambio de código tarda unos 3 a 5 minutos. Flujo recomendado:
rama → push → pull request → CI en verde → merge → aprobar. Empujar directo a `main` también
despliega, pero te salta la revisión del plan. Para volver atrás, haz `git revert` del commit en
`main` y aprueba el despliegue. `deploy.yml` también se puede lanzar a mano desde Actions → Run workflow.

**Primer uso:** ejecuta `deploy.yml` y luego `load-data.yml`. En la consola de API Gateway abre
*API keys* y muestra la llave; llama a `https://<api-id>.execute-api.us-east-1.amazonaws.com/v1/...`
con el encabezado `x-api-key`. (Swagger en `/docs` no funciona a través de API Gateway; usa la
tabla de endpoints de arriba.)

**Costo:** RDS `db.t4g.micro` entra en el free tier. No hay NAT Gateway; el endpoint de Secrets
Manager cuesta unos 7 USD al mes. Ejecuta `destroy.yml` cuando termines.

El rol IAM que usa GitHub Actions tiene `AdministratorAccess` porque Terraform crea roles IAM, una
VPC y más. Su confianza se limita a este único repositorio; conviene reducir la política antes de
cualquier uso real.

## Evolución Big Data

Esta PoC ya sigue un patrón medallion ligero:

| Capa | En esta PoC | A escala |
|---|---|---|
| Bronce (crudo) | Archivos CSV en `S3 · raw`, más el JSON original de cada fila rechazada en `rejected_records` | Tablas Iceberg crudas en S3, solo de anexado (append-only), alimentadas por CDC o streams |
| Plata (limpio) | Tablas validadas `departments`, `jobs`, `hired_employees` | Tablas Iceberg limpias y conformadas; validación como jobs de Spark/Glue con chequeos de calidad |
| Oro (negocio) | Los dos endpoints de análisis (SQL) | Tablas o vistas Iceberg agregadas, servidas a BI |

**Arquitectura objetivo si crece el volumen o el número de consumidores:**

```
RDS PostgreSQL ──CDC (DMS)──► Bronce ──valida/limpia──► Plata ──agrega──► Oro ──► Athena · Snowflake · BI
 (sistema de registro)        Iceberg en S3 (Glue Data Catalog, Lake Formation para gobierno)
```

- **Mantener PostgreSQL como sistema de registro** para la ingesta y las restricciones. El lake
  se añade aguas abajo, alimentado por captura de cambios (AWS DMS), en lugar de reemplazar la base.
- **Iceberg sobre S3** aporta tablas ACID, evolución de esquema y viaje en el tiempo. Los
  snapshots sustituirían a los exports AVRO como mecanismo de backup y restore.
- **Snowflake o Redshift** como almacén analítico cuando muchos usuarios de BI consulten a la vez.
- **Ingesta en streaming** (Kinesis o MSK) en lugar de lotes REST si los eventos llegan de forma continua.
- **Orquestación y calidad:** Step Functions o MWAA para los pipelines, Deequ o Great
  Expectations para las reglas de calidad de datos.

**Cuándo dar el paso:** decenas de millones de filas o más, varios sistemas de origen, o muchos
consumidores analíticos. **Por qué no ahora:** el reto es una carga transaccional pequeña que
necesita llaves foráneas aplicadas y pide explícitamente priorizar la claridad sobre el
sobrediseño. Snowflake no hace cumplir las llaves foráneas, así que la validación seguiría en
la aplicación de todos modos.
