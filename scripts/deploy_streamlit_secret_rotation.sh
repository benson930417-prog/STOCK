#!/usr/bin/env bash
set -euo pipefail

backup=/var/backups/stock-security/streamlit-secrets-20260812
install -d -o root -g root -m 0700 "$backup"
cp -a /home/ubuntu/STOCK/.streamlit/secrets.toml "$backup/secrets.toml.before"
cp -a /home/ubuntu/STOCK/app.py "$backup/app.py.before"

install -o ubuntu -g ubuntu -m 0600 \
  /tmp/streamlit-secrets.toml.new \
  /home/ubuntu/STOCK/.streamlit/secrets.toml
install -o ubuntu -g ubuntu -m 0644 \
  /tmp/app.py.hardened \
  /home/ubuntu/STOCK/app.py
rm -f /tmp/streamlit-secrets.toml.new /tmp/app.py.hardened

systemctl restart stock-dashboard.service

