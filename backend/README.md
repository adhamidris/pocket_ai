# Pocket AI Backend

Minimal FastAPI scaffold prepared for future feature work. The project currently exposes a single `/healthz` endpoint alongside core wiring such as structured logging, settings management, and Alembic migrations.

## Prerequisites
- Python 3.11+
- PostgreSQL 14+

## Installation
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
pip install -e .
pip install -e .[dev]
```

## Database Setup
The default connection string targets a local database named `pocket_db_1` owned by the `postgres` superuser. Recreate it on fresh environments as needed:
```bash
createdb -h localhost -U postgres pocket_db_1
psql -h localhost -U postgres -d postgres -c "GRANT ALL PRIVILEGES ON DATABASE pocket_db_1 TO postgres;"
```

## Running the Development Server
```bash
uvicorn app.main:app --reload
```
Then open `http://127.0.0.1:8000/healthz` to verify the service status.

## Database Migrations
Alembic is initialized with an empty baseline. Generate and apply migrations once models are available:
```bash
alembic revision -m "describe change"
alembic upgrade head
```

## Testing
```bash
pytest
```

## Linting & Formatting
```bash
ruff check app tests
black app tests
mypy app
```
