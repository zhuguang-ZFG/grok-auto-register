#!/bin/bash
# Run on VPS: install systemd unit + restart policy for CLIProxy podman container.
set -e

podman update --restart=always cliproxyapi 2>/dev/null || true

cat >/etc/systemd/system/cliproxyapi.service <<'UNIT'
[Unit]
Description=CLIProxyAPI (podman)
After=network-online.target
Wants=network-online.target

[Service]
Restart=always
RestartSec=5
ExecStartPre=-/usr/bin/podman rm -f cliproxyapi
ExecStart=/usr/bin/podman run --rm --name cliproxyapi \
  -p 127.0.0.1:8317:8317 \
  -p 100.103.82.78:8317:8317 \
  -v /opt/cliproxyapi/config.yaml:/CLIProxyAPI/config.yaml:Z \
  -v /opt/cliproxyapi/auth:/root/.cli-proxy-api:Z \
  docker.io/eceasy/cli-proxy-api:latest
ExecStop=/usr/bin/podman stop -t 5 cliproxyapi

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable cliproxyapi.service
systemctl is-enabled cliproxyapi.service

echo "--- listen ---"
ss -tlnp | grep 8317 || true

K=$(grep -oE "sk-cpa-[A-Za-z0-9]+" /opt/cliproxyapi/config.yaml | head -1)
echo "--- probe local ---"
curl -s -o /dev/null -w "http_local=%{http_code}\n" -H "Authorization: Bearer $K" http://127.0.0.1:8317/v1/models
