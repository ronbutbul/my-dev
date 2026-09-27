# Build stage
FROM node:18-alpine AS builder

WORKDIR /app

# Copy package files
COPY package*.json ./
COPY tsconfig.json ./
COPY webpack.config.js ./

# Install dependencies
RUN npm ci

# Copy source code
COPY public ./public
COPY src ./src

# Build the application
RUN npm run build

# Production stage
FROM nginx:alpine

# Copy built bundle.js from builder
COPY --from=builder /app/dist/bundle.js /usr/share/nginx/html/bundle.js

# Copy index.html from public directory
COPY --from=builder /app/public/index.html /usr/share/nginx/html/index.html

# Copy config.js template (env vars will be substituted at startup)
COPY --from=builder /app/public/config.js /usr/share/nginx/html/config.js.template

# Set proper permissions
RUN chmod -R 755 /usr/share/nginx/html && \
    chown -R nginx:nginx /usr/share/nginx/html

# Nginx SPA routing config
RUN echo 'server { \
    listen 80; \
    server_name _; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { \
        try_files $uri $uri/ /index.html; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80

# At startup: substitute env vars into config.js, then start nginx
CMD ["/bin/sh", "-c", "\
    sed \
      -e \"s|__STATS_API_URL__|${STATS_API_URL:-}|g\" \
      -e \"s|__KEYCLOAK_URL__|${KEYCLOAK_URL:-}|g\" \
      -e \"s|__KEYCLOAK_REALM__|${KEYCLOAK_REALM:-}|g\" \
      -e \"s|__KEYCLOAK_CLIENT_ID__|${KEYCLOAK_CLIENT_ID:-}|g\" \
      -e \"s|__GATEWAY_WS_URL__|${GATEWAY_WS_URL:-ws://localhost:9000}|g\" \
      /usr/share/nginx/html/config.js.template > /usr/share/nginx/html/config.js && \
    nginx -g 'daemon off;'"]
