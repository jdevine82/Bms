# Database access

Date: 1 Nov 2026. Status: decided.

The backend uses SQLAlchemy 2 (async, with asyncpg) and Alembic migrations. TimescaleDB-specific steps are raw SQL inside migrations.

Spec: docs/specs/m2-simulated-modbus-to-trend.md section 4
