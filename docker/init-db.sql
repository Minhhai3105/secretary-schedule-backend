-- Runs only on an empty volume. psql quotes the password as a SQL literal.
\getenv app_password APP_DB_PASSWORD
CREATE ROLE secretary_schedule_app LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER DATABASE secretary_schedule_dev OWNER TO secretary_schedule_app;
GRANT USAGE, CREATE ON SCHEMA public TO secretary_schedule_app;
