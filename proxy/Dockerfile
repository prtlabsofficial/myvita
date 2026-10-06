FROM nginxinc/nginx-unprivileged:1.29-alpine

COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY proxy_params /etc/nginx/proxy_params

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
