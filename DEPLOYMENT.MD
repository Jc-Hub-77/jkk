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

The frontend needs to know where to send API requests. The inline script added to each HTML file:
```html
<script>
  window.BACKEND_API_BASE_URL = 'http://127.0.0.1:8000'; // Default for local dev
</script>
```
needs to be adjusted for a production VPS deployment.

- **If using Nginx as a reverse proxy (as shown above) to serve the frontend and proxy API calls under the same domain:**
  The most straightforward approach is to set `window.BACKEND_API_BASE_URL = "";` (an empty string). This makes all frontend API calls use relative paths (e.g., `/api/v1/auth/login`). Nginx will then correctly route these requests to the backend service based on the `location /api/v1/` block.
  Modify the inline script in **all HTML files** in your deployment package (e.g., in `/var/www/trading_platform_frontend/`) to:
  ```html
  <script>
    window.BACKEND_API_BASE_URL = ""; 
  </script>
  ```
  This change can be done manually post-copying files to the VPS, or automated as part of a build/deployment script.

- **Alternative: Using a `config.js` file (more structured):**
  1. Create a file `frontend/js/config.js` with the content:
     ```javascript
     // frontend/js/config.js
     window.BACKEND_API_BASE_URL = ""; // For production with Nginx proxy as described
     // Or, if frontend and backend are on different subdomains/ports and not using the /api/v1 proxy path directly:
     // window.BACKEND_API_BASE_URL = "https://api.yourdomain.com"; 
     // window.BACKEND_API_BASE_URL = "https://yourdomain.com:8000"; // If exposing backend directly on a port (not recommended for production)
     ```
  2. In your production deployment, ensure this `config.js` file has the correct production URL.
  3. Modify all HTML files to remove the inline `<script>window.BACKEND_API_BASE_URL = ...</script>` and instead include:
     ```html
     <script src="js/config.js"></script> 
     ```
     This `<script>` tag for `config.js` should be placed **before** any other JavaScript files that rely on `window.BACKEND_API_BASE_URL` (e.g., `auth.js`, `dashboard.js`). Typically, this would be in the `<head>` element or at the very beginning of the `<body>`.

  For this project's current setup (with inline scripts already added), modifying the inline script is the direct path. The `config.js` approach is a good practice for future enhancements or more complex configurations.

### 5. Firewall and Security

- Configure a firewall on your VPS (e.g., `ufw` on Ubuntu) to only allow traffic on necessary ports (e.g., 80 for HTTP, 443 for HTTPS, SSH port).
- Keep your VPS updated with security patches.
- Secure your Docker environment.
- Regularly back up your database.

By following these considerations, you can deploy the Trading Platform application effectively on a single VPS. Remember to adapt paths, domain names, and specific configurations to your environment.
