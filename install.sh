#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
    echo "Run: sudo bash install.sh YOUR_USERNAME [--replace-v100-service]" >&2
    exit 1
fi
target_user="${1:-${SUDO_USER:-}}"
[[ $target_user =~ ^[a-z_][a-z0-9_-]*\$?$ && $target_user != root ]] || { echo 'Supply a non-root local username' >&2; exit 1; }
id "$target_user" >/dev/null
[[ $# -le 2 && ${2:-} =~ ^(--replace-v100-service)?$ ]] || { echo 'Unknown installer option' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
command -v nvidia-smi >/dev/null || { echo 'Install your NVIDIA driver first.' >&2; exit 1; }
if ! /usr/bin/python3 -c "import gi; gi.require_version('Gtk','3.0'); gi.require_version('AyatanaAppIndicator3','0.1')" 2>/dev/null; then
    apt-get update
    apt-get install -y python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
fi
command -v visudo >/dev/null || { echo 'Install sudo first.' >&2; exit 1; }
sudo_file=$(mktemp)
trap 'rm -f "$sudo_file"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/v100set-power\n' "$target_user" > "$sudo_file"
visudo -cf "$sudo_file"
install -d -o root -g root -m 755 /opt/v100set/v100set /usr/local/libexec
for module in __init__.py app.py core.py; do
    install -o root -g root -m 644 "$source_dir/v100set/$module" "/opt/v100set/v100set/$module"
done
install -o root -g root -m 644 "$source_dir/run.py" /opt/v100set/run.py
install -o root -g root -m 644 "$source_dir/LICENSE" /opt/v100set/LICENSE
install -o root -g root -m 755 "$source_dir/packaging/v100set-power" /usr/local/libexec/v100set-power
install -o root -g root -m 440 "$sudo_file" "/etc/sudoers.d/v100set-$target_user"
printf '#!/bin/sh\nexec /usr/bin/python3 /opt/v100set/run.py "$@"\n' > /usr/local/bin/v100set
chown root:root /usr/local/bin/v100set
chmod 755 /usr/local/bin/v100set
install -D -o root -g root -m 644 "$source_dir/assets/v100set.svg" /usr/share/icons/hicolor/scalable/apps/v100set.svg
install -o root -g root -m 644 "$source_dir/packaging/v100set.desktop" /usr/share/applications/v100set.desktop
if [[ ${2:-} == --replace-v100-service ]]; then
    systemctl disable v100-power-limit.service
    echo 'Disabled the previous v100-power-limit boot service; its files and current limits are unchanged.'
fi
if command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi
echo "Installed v100set for $target_user. Launch v100set from the applications menu."
echo 'Autostart is opt-in. No GPU power limit was changed by the installer.'
