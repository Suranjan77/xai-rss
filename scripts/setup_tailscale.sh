#!/usr/bin/env bash
# Expose the idigest UI securely over your private Tailscale tailnet.
# The app stays bound to 127.0.0.1; `tailscale serve` proxies it over HTTPS to
# your own devices only (no public exposure, no open ports, no 0.0.0.0).
#
# Run this yourself (it needs sudo + a browser login):
#     ! ./scripts/setup_tailscale.sh
set -e

if ! command -v tailscale >/dev/null 2>&1; then
  echo ">> installing tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

sudo systemctl enable --now tailscaled
sudo tailscale up        # opens a browser / prints an auth URL — log in

echo ">> exposing idigest UI (127.0.0.1:8081) over the tailnet with HTTPS"
# modern syntax; falls back to the older form if needed
sudo tailscale serve --bg 8081 \
  || sudo tailscale serve --bg --https=443 http://127.0.0.1:8081

echo
echo ">> Your private URL (open from any device signed into your tailnet):"
tailscale serve status 2>/dev/null || true
echo "   host:  $(tailscale status --json 2>/dev/null | grep -m1 '"DNSName"' | sed 's/.*: "//; s/".*//')"
echo "   ipv4:  $(tailscale ip -4 2>/dev/null | head -1)"
echo
echo "Next: put that https URL in config.local.toml as email.ui_base_url so the"
echo "email 'Open in browser' links work remotely, and enable web.auth_user/"
echo "auth_password for defense-in-depth."
