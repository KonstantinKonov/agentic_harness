-- Runs once on first Postgres container init (mounted into /docker-entrypoint-initdb.d).
-- The first database (POSTGRES_DB, default "langfuse") is created by the container itself;
-- here we add the second database used by the LangGraph Postgres checkpointer.
CREATE DATABASE app_checkpointer;
