-- Run once per database cluster as a superuser / admin role BEFORE migrations.
-- psql -v owner_password=... -v app_password=... -f deploy/db/bootstrap.sql
-- copperbelt      : owns the schema, runs migrations. Never used by the running app.
-- copperbelt_app  : used by the API and workers. Not owner, not superuser => RLS applies.
\set ON_ERROR_STOP on
SELECT format('CREATE ROLE copperbelt LOGIN PASSWORD %L', :'owner_password')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copperbelt') \gexec
SELECT format('CREATE ROLE copperbelt_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L', :'app_password')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copperbelt_app') \gexec
SELECT 'CREATE DATABASE copperbelt OWNER copperbelt'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'copperbelt') \gexec
