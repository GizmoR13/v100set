#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
rm -f /usr/local/bin/v100set /usr/local/libexec/v100set-power
rm -f /etc/sudoers.d/v100set-*
rm -f /usr/share/applications/v100set.desktop /usr/share/icons/hicolor/scalable/apps/v100set.svg
rm -rf /opt/v100set
echo 'Application removed. User settings are preserved.'
echo 'Remove ~/.config/autostart/v100set.desktop and ~/.config/v100set if no longer needed.'
echo 'GPU limits were not changed. A previously disabled boot service is not automatically re-enabled.'
