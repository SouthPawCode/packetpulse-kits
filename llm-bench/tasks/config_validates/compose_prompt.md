Write a docker-compose.yml (Compose Specification; a version key is not needed) for a small web app stack.

Requirements:
- Service `db`: image postgres:16 with environment POSTGRES_USER=app, POSTGRES_PASSWORD=change-me, POSTGRES_DB=app. Its data directory /var/lib/postgresql/data is stored in a named volume called `dbdata`. It has a healthcheck that uses pg_isready.
- Service `web`: image ghcr.io/example/web:1.4.2 with environment DATABASE_URL=postgresql://app:change-me@db:5432/app. It publishes host port 8080 to container port 8000. It must start only after `db` is healthy (depends_on with condition service_healthy).
- Both services restart with the policy `unless-stopped`.
- Both services are attached to a user-defined network called `backend`.
- No env_file, no build sections.

Return only the file in one fenced yaml code block.
