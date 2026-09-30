Write an nginx configuration file that will be saved as /etc/nginx/conf.d/default.conf in the official nginx container. It contains server blocks only: no http{} wrapper and no events{} block.

Requirements:
- Site name: app.lab.example
- Port 80 redirects permanently (301) to the same host and URI on HTTPS.
- Port 443 serves TLS using /etc/nginx/certs/fullchain.pem and /etc/nginx/certs/privkey.pem. Allow TLS 1.2 and 1.3 only.
- Every request is reverse proxied to the backend at http://192.168.40.15:8080. Use that IP address, not a hostname.
- Pass these headers to the backend: Host, X-Real-IP, X-Forwarded-For, X-Forwarded-Proto.
- The path /ws/ is a WebSocket endpoint: use HTTP/1.1, pass the Upgrade and Connection headers, and set proxy_read_timeout to 3600s for it.
- client_max_body_size is 50m.
- Enable gzip for text/css, application/json and application/javascript.
- Send a Strict-Transport-Security header (max-age=31536000) on HTTPS responses.

Return only the configuration in one fenced code block.
