```yaml
services:
  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: change-me
      POSTGRES_DB: app
    volumes:
      - dbdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 3s
      retries: 10
    networks:
      - backend

  web:
    image: ghcr.io/example/web:1.4.2
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://app:change-me@db:5432/app
    ports:
      - "8080:8000"
    depends_on:
      db:
        condition: service_healthy
    networks:
      - backend

volumes:
  dbdata:

networks:
  backend:
```
