# Deployment Guide for Trading Platform

This document provides guidance on deploying the Trading Platform application.

## Overview

The application consists of several components:
- **Backend API:** Built with FastAPI, serving as the core logic.
- **Celery Workers:** For handling asynchronous tasks (e.g., live trading, email sending).
- **Database:** PostgreSQL is recommended for storing application data.
- **Message Broker:** Redis is used for Celery task queuing.
- **Frontend:** A static single-page application (SPA) built with HTML, CSS, and JavaScript.

## General Deployment Strategies

Multiple deployment strategies are possible, including:
- Docker Compose for local development and simple deployments.
- Kubernetes for scalable production deployments.
- Platform-as-a-Service (PaaS) solutions.

This guide will focus on Docker Compose for the backend and provide considerations for various environments.

## Backend Deployment with Docker Compose

The backend services (FastAPI web server, Celery workers, PostgreSQL database, Redis message broker) are containerized and can be orchestrated using Docker Compose.

Refer to the `docker-compose.yml` file in the project root for the service definitions.

Key aspects:
- **Database (`db` service):** Uses the official PostgreSQL image. Data is persisted in a Docker volume.
- **Redis (`redis` service):** Uses the official Redis image.
- **Web API (`web` service):** Builds from the `backend/` directory. Exposes the API (e.g., on port 8000).
- **Celery Worker (`worker` service):** Builds from the `backend/` directory. Connects to Redis and the database.

**Environment Variables:**
- A `.env` file (copied from `.env.example`) in the `backend/` directory is crucial for configuring the backend services, including database connection strings, JWT secrets, API keys, and SMTP settings.
- Ensure `STRATEGIES_DIR` is correctly set in the `.env` file to point to the directory containing strategy Python files, especially if it's outside the Docker build context or mounted differently. For Docker deployments, this usually involves mounting a volume.

**Running with Docker Compose:**
```bash
# Build and start all services in detached mode
docker-compose up --build -d

# Stop services
docker-compose down

# View logs
docker-compose logs -f <service_name> # e.g., web, worker
```

## Production Deployment with Docker Compose (`docker-compose.prod.yml`)

For production or production-like staging environments, a separate Docker Compose file, `docker-compose.prod.yml`, is provided. This configuration is optimized for security and production best practices.

**Key Differences from `docker-compose.yml` (Development):**

*   **No Source Code Volume Mounts:** Services use the code baked into their Docker images during the build process (e.g., `docker-compose -f docker-compose.prod.yml build web worker`). This ensures that the deployed code is exactly what was tested and built.
*   **Externalized Environment Variables:** Configuration relies entirely on environment variables provided by the deployment platform or CI/CD system. The `.env` file is NOT used. This is crucial for security and flexibility.
    *   **Critical Variables to Set Externally:**
        *   `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
        *   `JWT_SECRET_KEY` (must be strong and unique)
        *   `API_ENCRYPTION_KEY` (must be strong and unique for encrypting exchange API keys)
        *   `FRONTEND_URL` (e.g., `https://yourdomain.com`)
        *   `ALLOWED_ORIGINS` (e.g., `https://yourdomain.com,https://www.yourdomain.com`)
        *   `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAILS_FROM_EMAIL` (for email notifications)
        *   `COINBASE_COMMERCE_API_KEY`, `COINBASE_COMMERCE_WEBHOOK_SECRET` (if using Coinbase Commerce)
        *   `STRATEGIES_DIR` (if strategies are not located at `/app/strategies` within the image)
        *   `GUNICORN_WORKERS` (e.g., `4`)
        *   `CELERY_CONCURRENCY` (e.g., `8` per worker instance, as used in `backend/Dockerfile.worker`)
        *   `CELERY_WORKER_REPLICAS` (e.g., `2` for Docker Swarm scaling, defined in `docker-compose.prod.yml` under `worker.deploy.replicas`)
        *   `ENVIRONMENT` (should be set to `production`)
*   **Gunicorn for Web Service:** The `web` service (backend API) is managed by Gunicorn, as configured in `docker-compose.prod.yml` and `backend/Dockerfile.web`. Gunicorn acts as a process manager for Uvicorn workers, enhancing scalability and robustness for production. Key Gunicorn settings (like number of workers, worker class, timeouts) can be configured via `backend/gunicorn_conf.py` and overridden by environment variables (e.g., `GUNICORN_WORKERS`).
*   **Celery Worker Scaling:**
    *   **Concurrency per Worker:** The number of concurrent tasks each Celery worker process can handle is defined by the `CELERY_CONCURRENCY` environment variable. This is used in the `CMD` of `backend/Dockerfile.worker` (e.g., `${CELERY_CONCURRENCY:-4}`).
    *   **Number of Worker Instances (Replicas):** For environments like Docker Swarm, the number of `worker` service replicas (instances) can be scaled using the `deploy: replicas:` setting in `docker-compose.prod.yml`. This allows you to run multiple Celery worker containers, each potentially handling `CELERY_CONCURRENCY` tasks.
*   **Restricted Port Exposure:** Database (`db`) and Redis (`redis`) services do not expose their ports to the host by default. They are accessible within the Docker internal network. The `web` service exposes its port `8000` only on `127.0.0.1` by default, expecting a reverse proxy (like Nginx) to manage public access.
*   **Separate Production Volumes:** Uses `postgres_data_prod` and `redis_data_prod` for persistent data, keeping production data separate from development.

**Running with `docker-compose.prod.yml`:**

```bash
# Build and start all services in detached mode using the production configuration
docker-compose -f docker-compose.prod.yml up -d --build

# Stop services
docker-compose -f docker-compose.prod.yml down

# View logs
docker-compose -f docker-compose.prod.yml logs -f <service_name>
```

**Secrets Management:**

While `docker-compose.prod.yml` expects secrets to be passed as environment variables, this approach might not be suitable for all production environments if the platform does not offer secure injection of these variables.
Refer to `backend/SECRETS.MD` for more comprehensive recommendations on secrets management, such as using:
*   Cloud provider secrets management services (e.g., AWS Secrets Manager, Google Secret Manager, Azure Key Vault).
*   HashiCorp Vault.
*   Docker Swarm secrets or Kubernetes Secrets.

It is critical to ensure that sensitive data like `JWT_SECRET_KEY` and `API_ENCRYPTION_KEY` are generated securely and managed appropriately throughout their lifecycle. The `ENVIRONMENT: production` setting in `docker-compose.prod.yml` enables stricter checks in `backend/config.py` for these critical variables.

## Frontend Deployment

The frontend is a static application located in the `frontend/` directory. It needs to be served by a web server like Nginx or Apache, or hosted on a static site hosting service (e.g., Netlify, Vercel, AWS S3 + CloudFront).

When deploying, ensure the `window.BACKEND_API_BASE_URL` in the frontend's HTML/JavaScript is correctly configured to point to the deployed backend API.

---

## Single VPS Deployment Considerations

This section outlines specific considerations for deploying the entire application (frontend and backend) on a single Virtual Private Server (VPS).

### 1. Resource Planning

Running all components (PostgreSQL, Redis, Backend Web server, Celery workers, and a web server like Nginx for the frontend) on a single VPS can be resource-intensive.
- **CPU & RAM:** The FastAPI backend, Celery workers (especially if running multiple strategies), and PostgreSQL can consume significant CPU and RAM. A VPS with at least 2 CPU cores and 4GB RAM is advisable as a starting point, but this depends heavily on the number of users, active strategies, and data volume.
- **Disk Space:** Ensure sufficient disk space for the operating system, Docker images, database data (PostgreSQL volume), application logs, and frontend files.
- **Monitoring:** It's crucial to monitor resource usage (CPU, RAM, disk I/O, network traffic) post-deployment and scale the VPS resources if bottlenecks are identified. Tools like `htop`, `docker stats`, and Prometheus with Grafana can be helpful.

### 2. Backend Services with Docker Compose

Docker Compose remains a convenient way to manage and run the backend services (PostgreSQL, Redis, FastAPI web app, Celery workers) on the VPS.
- Follow the general Docker Compose instructions mentioned earlier.
- Ensure the backend's API port (e.g., 8000) is mapped to `127.0.0.1:8000` in your `docker-compose.yml`. This is important so that Nginx (running on the host or as another container) can securely proxy requests to it without exposing the backend API directly to the public internet. Example `ports` configuration in `docker-compose.yml` for the `web` service:
  ```yaml
  services:
    web:
      # ... other web service configurations
      ports:
        - "127.0.0.1:8000:8000" # Map port 8000 of the container to port 8000 on the host's loopback interface
  ```

### 3. Frontend Deployment with Nginx

Nginx can be installed directly on the VPS host to serve the static frontend files and act as a reverse proxy for the backend API. This setup simplifies SSL/TLS certificate management for your domain.

- **Install Nginx:** Use your VPS distribution's package manager (e.g., `sudo apt update && sudo apt install nginx`).
- **Copy Frontend Files:** Copy all files and subdirectories from your project's `frontend/` directory to a location on your VPS, for example, `/var/www/trading_platform_frontend/`.
  ```bash
  # Example:
  # On your local machine, navigate to your project root
  # scp -r frontend/ user@your_vps_ip:/tmp/frontend_build
  # On your VPS:
  # sudo mkdir -p /var/www/trading_platform_frontend
  # sudo cp -r /tmp/frontend_build/* /var/www/trading_platform_frontend/
  ```
- **Nginx Configuration Example:**
  Create or edit an Nginx server block configuration file (e.g., `/etc/nginx/sites-available/trading_platform` and symlink it to `/etc/nginx/sites-enabled/`).

  ```nginx
  server {
      listen 80;
      server_name yourdomain.com www.yourdomain.com; # Replace with your actual domain or VPS IP address

      # Serve Frontend Static Files
      location / {
          root /var/www/trading_platform_frontend; # Path where frontend files are copied
          index index.html index.htm;
          try_files $uri $uri/ /index.html; # Important for Single Page Applications (SPAs)
      }

      # Proxy API requests to the backend Docker container
      # All requests starting with /api/v1/ will be forwarded to the backend.
      location /api/v1/ {
          proxy_pass http://127.0.0.1:8000; # Backend API running via Docker Compose on host's port 8000
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
          proxy_http_version 1.1;
          proxy_set_header Upgrade $http_upgrade; # For WebSocket support if needed by API
          proxy_set_header Connection "upgrade";
          proxy_read_timeout 300s; # Increase timeout for potentially long-running API requests
          proxy_send_timeout 300s;
      }

      # SSL/TLS Configuration (Recommended for Production)
      # Ensure you have obtained SSL certificates (e.g., via Let's Encrypt)
      # listen 443 ssl http2;
      # server_name yourdomain.com www.yourdomain.com;
      #
      # ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
      # ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
      # include /etc/letsencrypt/options-ssl-nginx.conf; # Recommended SSL parameters
      # ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem; # Diffie-Hellman parameter for DHE ciphersuites
      #
      # # Redirect HTTP to HTTPS
      # if ($scheme != "https") {
      #     return 301 https://$host$request_uri;
      # }
  }
  ```
  - After creating/editing the Nginx configuration, test it (`sudo nginx -t`) and reload Nginx (`sudo systemctl reload nginx`).

### 4. Configuring `window.BACKEND_API_BASE_URL` in Production

The frontend needs to know where to send API requests. This is managed centrally via the `frontend/js/config.js` file.

**Production Configuration (Standard):**
For production deployments, especially when using a reverse proxy like Nginx (as detailed in the "Frontend Deployment with Nginx" section) to serve the frontend and proxy API calls (e.g., under the `/api/v1/` path) from the same domain, `window.BACKEND_API_BASE_URL` in `frontend/js/config.js` **must be an empty string**:
```javascript
// frontend/js/config.js
// Correct for production with a reverse proxy on the same domain:
window.BACKEND_API_BASE_URL = ""; 
```
This setup ensures that all frontend API calls use relative paths (e.g., `fetch('/api/v1/auth/login')`). The reverse proxy (Nginx) is then responsible for correctly routing these requests to the backend service (e.g., `http://127.0.0.1:8000` as per `docker-compose.prod.yml`). This is the standard and most secure method.

**Local Development or Different Domains:**
For local development, or if the backend API is hosted on a completely different domain or port than the frontend in production (not recommended for simplicity and to avoid CORS complexities if possible), `frontend/js/config.js` would be temporarily modified:
```javascript
// Example for local development (backend on port 8000):
// window.BACKEND_API_BASE_URL = "http://127.0.0.1:8000";

// Example if API is on a different subdomain in production:
// window.BACKEND_API_BASE_URL = "https://api.yourdomain.com";
```
**Crucial Note:** The version of `frontend/js/config.js` committed to your version control system (e.g., Git) should **always** have `window.BACKEND_API_BASE_URL = "";`. This ensures that deployments default to the correct production-ready configuration. Developers should only change this locally for their specific needs and avoid committing such changes.

**HTML File Integration:**
All HTML files across the `frontend/` and `frontend/admin/` directories have been standardized to include the `config.js` script within their `<head>` section. This script tag is placed before any other application-specific JavaScript files that depend on `window.BACKEND_API_BASE_URL`.
- For files directly in `frontend/`: `<script src="js/config.js"></script>`
- For files in `frontend/admin/`: `<script src="../js/config.js"></script>`

This centralized `config.js` approach simplifies managing the backend API URL across different environments and is critical for a smooth production deployment.

### 5. Firewall and Security

- Configure a firewall on your VPS (e.g., `ufw` on Ubuntu) to only allow traffic on necessary ports (e.g., 80 for HTTP, 443 for HTTPS, SSH port).
- Keep your VPS updated with security patches.
- Secure your Docker environment.
- Regularly back up your database.

By following these considerations, you can deploy the Trading Platform application effectively on a single VPS. Remember to adapt paths, domain names, and specific configurations to your environment.
