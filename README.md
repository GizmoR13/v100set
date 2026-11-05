# v100set

A small Linux tray app for live NVIDIA GPU power limits. Built for Tesla V100 workstations; shows only V100/V100S GPUs reported by `nvidia-smi`.

Each V100 has a card with **live power draw on the left**, a **power-limit slider in the middle**, and **temperature on the right**. Sliders snap to 5 W increments, within the range reported by the driver. Missing sensor readings show a dash; unsupported power controls are disabled.

![v100set control panel with eight simulated V100 GPUs](assets/preview.png)

*Preview uses simulated telemetry.*

## Install (Ubuntu 24.04 / Debian-family desktops)

Requires an installed NVIDIA driver, a graphical desktop and a tray/AppIndicator host. Ubuntu's default GNOME desktop includes the AppIndicator extension; other GNOME installations may need to enable it. The control panel remains available from the applications menu even without a tray host.

```bash
git clone https://github.com/GizmoR13/v100set.git
cd v100set
sudo bash install.sh "$USER"
v100set
```

The installer adds GTK 3 and Ayatana AppIndicator bindings when missing. It does not install CUDA, PyTorch or a driver. This desktop utility uses distribution Python/GTK packages, without modifying AI environments.

## Use

- Supports 1–8 V100 GPUs: up to four in one column, then a second column (cards 5–8).
- Resize the window to scale text and controls. The last window size is restored on the next launch.
- **Link all GPU sliders** applies each slider change to all controllable V100 GPUs in 5 W steps, clamped to each GPU’s supported range. Enabling the checkbox alone does not change limits; LOAD still restores each card’s individual saved limit. The checkbox preference is remembered.


- Open the tray icon → **Power controls**. On supported desktops, middle-click opens the panel directly. AppIndicator hosts own the tray menu, so opening it is not a custom slider popup inside the system volume menu.
- Drag a slider to change the limit live. Changes are debounced by 400 ms and verified against the driver. GPU identity uses UUID, never a possibly changing index.
- **GPU clock limit** selects a supported core-clock ceiling (or Auto). The GPU may run below the ceiling; memory clocks are unchanged. **Reset (Auto) clock**, next to Hide, resets all connected V100 core clocks.
- **Applied: … W, … MHz** shows the driver power limit and live measured frequency, not a guaranteed fixed clock.
- Linking sliders applies power changes to other power sliders and clock changes to other clock sliders.
- **SAVE** stores the selected limits in `~/.config/v100set/settings.json`.
- **LOAD** reads power and clock values (including Auto) and applies them to matching V100 GPUs. Older saved limits are rounded to the nearest supported 5 W step; entries for other cards are ignored. Missing devices and invalid saved limits are reported rather than assigned to a different card.
- **Start at login and apply saved limits** installs an XDG desktop autostart entry. When enabled, the app starts hidden in the tray and restores saved limits after graphical login, retrying initial GPU discovery briefly.
- Unchecking autostart removes that entry. v100set will neither launch nor apply limits at the next login. It does **not** reset limits currently applied, or disable settings from unrelated software.
- Closing or hiding the window keeps the tray app running. **Quit v100set** exits; already applied limits remain managed by the driver.

SAVE does not turn on autostart. Enabling autostart does not silently overwrite your saved profile: press SAVE to choose the limits to restore. Launching the app manually only reads telemetry until you move a slider or press LOAD. This is **login autostart**, not a boot-time system service. Logging in is required for automatic restore.

Power limits are ceilings, not fixed power consumption. Lower limits can reduce performance. The app does not control cooling, memory clocks, voltage or persistence mode. Driver acceptance does not replace adequate cooling and power delivery.

## Permissions

The GUI runs as your normal user. A small root-owned helper at `/usr/local/libexec/v100set-power` is the only sudo-authorized command. A per-user sudoers rule permits that helper without a password so live adjustments and login restore work without prompts.

The helper uses isolated system Python, a fixed `nvidia-smi` path and a clean environment. It validates exactly two arguments, verifies the UUID and the GPU’s current minimum/maximum power limits, sets its power limit and reads it back. It also accepts a strictly validated `UUID clock MHz|auto` request for V100 cards: values must occur in the driver’s supported list; Auto resets only the core-clock policy. It accepts no command paths, configuration files, shell snippets or arbitrary NVIDIA operations. This grants the selected local user permission to change supported NVIDIA power limits; it is not a general sudo grant. Keep the helper and its parent directories root-owned.

Only V100/V100S GPUs discovered by the NVIDIA driver are shown; AMD/Intel power control is not implemented. A card can advertise a range yet reject a change due to device/driver restrictions; the app reports that error.

## Development and tests

```bash
python3 -m unittest discover -s tests -v
python3 tests/gui_smoke.py
python3 tests/gui_layout.py
python3 run.py
```

The tests use simulated GPU responses and temporary settings directories; they never change hardware limits. The GUI smoke test requires a graphical session and checks two V100 cards, exclusion of A400, 5 W snapping, window reopening, slider debouncing, LOAD/SAVE, the autostart toggle and hidden startup restoration. Real hardware acceptance should include telemetry and a small reversible limit change.

Runtime: Python 3.10+, PyGObject, GTK 3, Ayatana AppIndicator, `sudo`, `nvidia-smi`. Tested on Ubuntu 24.04 with X11; other desktop/tray hosts may present activation differently.

## Uninstall

Quit the app, uncheck autostart, then run `sudo bash uninstall.sh`. Settings are retained. Uninstallation does not reset the GPUs or automatically restore previous boot services.

## License

MIT. See [LICENSE](LICENSE).

### Clock state

Clock changes require the updated root helper. The driver acknowledges the requested clock policy; measured MHz is not a readback of the ceiling. NVIDIA does not expose the locked-clock range through the telemetry used here, so a fresh manual launch shows “select / Auto” until a clock command or LOAD succeeds. SAVE includes only clock choices acknowledged during this session, retaining earlier saved choices for other cards. Existing power-only profiles remain valid and do not change clocks. Clock ceilings reset on reboot/driver reload; enabled login restore reapplies saved choices.
