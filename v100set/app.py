"""GTK panel with an AppIndicator tray entry."""

import argparse
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import AyatanaAppIndicator3 as Indicator, Gio, GLib, Gtk, Gdk

from .core import Settings, apply_limit, discover, load_targets, snap_limit, is_v100, apply_clock

CSS = b"""
window { background: #171b24; color: #eef2f8; }
.panel { padding: 20px; }
.title { font-size: 22px; font-weight: bold; }
.muted { color: #9caac1; font-size: 11px; }
.card { background: #222938; border-radius: 12px; padding: 14px; }
.gpu-name { font-weight: bold; font-size: 13px; }
.reading { font-weight: bold; font-size: 16px; }
scale { padding: 2px 0; }
scale trough { background: #3a455a; min-height: 6px; border-radius: 6px; }
scale highlight { background: #67d5b5; border-radius: 6px; }
scale slider { background: #e8fff7; min-width: 15px; min-height: 15px; border-radius: 50%; }
scale value { color: #8ee4c9; font-weight: bold; }
button { background: #313d52; color: #eef2f8; border: none; border-radius: 8px; padding: 8px 12px; }
button:hover { background: #43536e; }
.status { font-size: 11px; color: #bbc9dc; }
"""


def label(text, style=None, xalign=0):
    widget = Gtk.Label(label=text, xalign=xalign)
    if style:
        widget.get_style_context().add_class(style)
    return widget


class Card(Gtk.Box):
    def __init__(self, gpu, changed, clock_changed):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.get_style_context().add_class("card")
        self.gpu = gpu
        self.block = False
        self.clock_known = False
        self.clock_target = None
        self.pack_start(label(gpu.name, "gpu-name"), False, False, 0)
        self.pack_start(label(f"PCI {gpu.bus} · {gpu.uuid[-8:]}", "muted"), False, False, 0)
        row = self.row = Gtk.Box(spacing=12)
        self.draw = label("— W", "reading")
        self.draw.set_size_request(75, -1)
        self.temp = label("— °C", "reading", 1)
        self.temp.set_size_request(60, -1)
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                             gpu.minimum if gpu.adjustable else 0,
                                             gpu.maximum if gpu.adjustable else 1, 5)
        self.scale.set_digits(0)
        self.scale.set_value_pos(Gtk.PositionType.TOP)
        self.scale.connect("format-value", lambda _, value: f"{value:.0f} W limit")
        self.scale.set_hexpand(True)
        self.scale.set_size_request(220, -1)
        self.scale.set_sensitive(gpu.adjustable)
        self.scale.set_tooltip_text("Changes apply live. SAVE stores limits for later use.")
        if gpu.adjustable:
            self.scale.set_tooltip_text(f"Range: {gpu.minimum:g}–{gpu.maximum:g} W · 5 W steps. Changes apply live.")
            self.scale.set_value(gpu.limit)
        self.scale.connect("value-changed", lambda widget: None if self.block else changed(gpu.uuid, widget.get_value()))
        row.pack_start(self.draw, False, False, 0)
        sliders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        sliders.pack_start(self.scale, False, False, 0)
        self.clock_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, max(1, len(gpu.clock_options)), 1)
        self.clock_scale.set_digits(0)
        self.clock_scale.set_value_pos(Gtk.PositionType.TOP)
        self.clock_scale.set_hexpand(True)
        self.clock_scale.set_sensitive(bool(gpu.clock_options))
        self.clock_scale.connect("format-value", self.clock_text)
        self.clock_scale.connect("value-changed", lambda widget: None if self.block else clock_changed(gpu.uuid, self.clock_value()))
        sliders.pack_start(self.clock_scale, False, False, 0)
        row.pack_start(sliders, True, True, 0)
        row.pack_start(self.temp, False, False, 0)
        self.pack_start(row, False, False, 0)
        self.detail = label("", "muted")
        self.pack_start(self.detail, False, False, 0)
        self.update(gpu, False)

    def clock_value(self):
        index = round(self.clock_scale.get_value())
        return None if index == 0 else self.gpu.clock_options[min(index - 1, len(self.gpu.clock_options) - 1)]

    def clock_text(self, _scale, value):
        if not self.gpu.clock_options:
            return "GPU clock unavailable"
        if not self.clock_known:
            return "GPU clock limit: select / Auto"
        index = round(value)
        return "GPU clock: Auto" if index == 0 else f"GPU clock limit: {self.gpu.clock_options[min(index - 1, len(self.gpu.clock_options) - 1)]} MHz"

    def set_clock_value(self, value, known=True):
        self.clock_known = known
        self.block = True
        self.clock_scale.set_value(0 if value is None else self.gpu.clock_options.index(value) + 1)
        self.clock_scale.queue_draw()
        self.block = False

    def set_value(self, value):
        self.block = True
        self.scale.set_value(value)
        self.block = False

    def update(self, gpu, pending):
        self.gpu = gpu
        self.draw.set_text("— W" if gpu.draw is None else f"{gpu.draw:.1f} W")
        self.temp.set_text("— °C" if gpu.temperature is None else f"{gpu.temperature:.0f} °C")
        clock = "—" if gpu.clock is None else f"{gpu.clock:g}"
        self.detail.set_text(("Power limit unavailable" if not gpu.adjustable else f"Applied: {gpu.limit:g} W, {clock} MHz") + (" · Applying…" if pending else ""))
        self.detail.set_tooltip_text("Watts: driver power limit. MHz: live measured GPU clock; it may be below the selected ceiling.")
        self.clock_scale.set_sensitive(bool(gpu.clock_options))
        self.clock_scale.set_range(0, max(1, len(gpu.clock_options)))
        if not pending and gpu.adjustable and not self.scale.has_focus():
            self.set_value(gpu.limit)


class App(Gtk.Application):
    def __init__(self, startup=False):
        super().__init__(application_id="io.github.v100set", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.startup = startup
        self.settings = Settings()
        self.window = None
        self.cards = {}
        self.detected = []
        self.pending = {}
        self.clock_pending = {}
        self.clock_busy = set()
        self.clock_timers = {}
        self.clock_applied = {}
        self.timers = {}
        self.busy = set()
        self.polling = False
        self.loading = False
        self.load_errors = []
        self.poll_pool = ThreadPoolExecutor(max_workers=1)
        self.apply_pool = ThreadPoolExecutor(max_workers=1)
        self.attempts = 0
        self.revision = 0
        self.closed = False
        self.poll_source = None
        self.resize_source = None
        self.layout_ids = ()
        self.ui_scale = 1.0
        self.last_size = None
        self.connect("activate", self.activate)
        self.connect("shutdown", self.cleanup)

    def message(self, text):
        self.status.set_text(text)
        self.status.set_tooltip_text(text)

    def activate(self, _app):
        if self.window:
            self.show_panel()
            return
        try:
            data = self.settings.read()
        except (ValueError, OSError) as exc:
            data = {"start_at_login": False}
            error = str(exc)
        else:
            error = None
        if self.startup and not data["start_at_login"]:
            self.quit()
            return
        self.restore_on_start = self.startup
        self.hold()
        provider = self.provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self, title="v100set")
        self.window.set_wmclass("v100set", "v100set")
        self.window.set_icon_name("v100set")
        size = data.get("window_size")
        self.saved_size = (size if isinstance(size, list) and len(size) == 2
                           and all(type(v) is int and 350 <= v <= 8000 for v in size) else None)
        self.window.set_default_size(*(self.saved_size or [530, 600]))
        self.window.set_resizable(True)
        self.window.connect("configure-event", self.resized)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.set_keep_above(True)
        self.window.connect("delete-event", lambda *_: self.hide())
        outer = self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.get_style_context().add_class("panel")
        self.window.add(outer)
        outer.pack_start(label("NVIDIA POWER CONTROL", "muted"), False, False, 0)
        header = self.header = Gtk.Box(spacing=10)
        header.pack_start(label("v100set", "title"), True, True, 0)
        hide = Gtk.Button(label="Hide")
        hide.connect("clicked", lambda *_: self.hide())
        header.pack_end(hide, False, False, 0)
        self.reset_clock_button = Gtk.Button(label="Reset (Auto) clock")
        self.reset_clock_button.connect("clicked", lambda *_: self.reset_clocks())
        header.pack_end(self.reset_clock_button, False, False, 0)
        outer.pack_start(header, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(570)
        self.rows = Gtk.Grid(column_spacing=10, row_spacing=10, column_homogeneous=True, row_homogeneous=True)
        scroll.add(self.rows)
        outer.pack_start(scroll, True, True, 0)
        self.status = label(error or "Detecting NVIDIA GPUs…", "status")
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(65)
        outer.pack_start(self.status, False, False, 0)
        buttons = self.buttons = Gtk.Box(spacing=10, homogeneous=True)
        self.load_button = Gtk.Button(label="LOAD")
        self.save_button = Gtk.Button(label="SAVE")
        self.load_button.connect("clicked", lambda *_: self.load())
        self.save_button.connect("clicked", lambda *_: self.save())
        buttons.pack_start(self.load_button, True, True, 0)
        buttons.pack_start(self.save_button, True, True, 0)
        outer.pack_start(buttons, False, False, 0)
        self.link_check = Gtk.CheckButton(label="Link all GPU sliders")
        self.link_check.set_tooltip_text("Apply changes to all controllable V100 GPUs, within each card's supported range.")
        self.link_check.set_active(data.get("link_sliders") is True)
        self.link_check.connect("toggled", self.toggle_link)
        outer.pack_start(self.link_check, False, False, 0)
        self.start_check = Gtk.CheckButton(label="Start at login and apply saved limits")
        self.start_check.set_active(data["start_at_login"])
        self.start_check.connect("toggled", self.toggle_startup)
        outer.pack_start(self.start_check, False, False, 0)
        self.indicator = Indicator.Indicator.new(self.get_application_id().replace(".", "-"), "v100set", Indicator.IndicatorCategory.HARDWARE)
        self.indicator.set_status(Indicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("v100set")
        menu = Gtk.Menu()
        show = Gtk.MenuItem(label="Power controls")
        show.connect("activate", lambda *_: self.activate(self))
        menu.append(show)
        self.indicator.set_secondary_activate_target(show)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit v100set")
        quit_item.connect("activate", lambda *_: self.quit())
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)
        outer.show_all()
        if not self.startup:
            self.show_panel()
        self.poll()
        self.poll_source = GLib.timeout_add_seconds(2, self.poll)

    def cleanup(self, _app):
        if self.resize_source:
            GLib.source_remove(self.resize_source)
        self.persist_size()
        self.closed = True
        if self.poll_source:
            GLib.source_remove(self.poll_source)
        for source in list(self.timers.values()) + list(self.clock_timers.values()):
            GLib.source_remove(source)
        self.timers.clear()
        if self.window:
            self.indicator.set_status(Indicator.IndicatorStatus.PASSIVE)
            self.window.destroy()

    def show_panel(self):
        self.window.show_all()
        self.window.deiconify()
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.present_with_time(Gtk.get_current_event_time())

    def hide(self):
        self.window.hide()
        return True

    def controls(self):
        idle = not self.pending and not self.busy and not self.clock_pending and not self.clock_busy
        self.reset_clock_button.set_sensitive(any(c.gpu.clock_options for c in self.cards.values()))
        self.save_button.set_sensitive(idle and any(c.gpu.adjustable for c in self.cards.values()))
        self.load_button.set_sensitive(idle and bool(self.cards))

    def poll(self):
        if self.closed:
            return False
        if not self.polling:
            self.polling = True
            self.poll_pool.submit(self.read_hardware)
        return True

    def read_hardware(self):
        revision = self.revision
        try:
            gpus, error = discover(), None
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            gpus, error = [], str(exc)
        except Exception as exc:
            gpus, error = [], f"Telemetry unavailable: {exc}"
        GLib.idle_add(self.refresh, gpus, error, revision)

    def refresh(self, gpus, error, revision):
        self.polling = False
        if self.closed:
            return False
        if revision != self.revision:
            self.poll()
            return False
        self.attempts += 1
        if error:
            self.message(f"Cannot read GPUs: {error}")
            for card in self.cards.values():
                card.scale.set_sensitive(False)
                card.draw.set_text("— W")
                card.temp.set_text("— °C")
            self.save_button.set_sensitive(False)
            self.load_button.set_sensitive(False)
        else:
            self.detected = gpus
            gpus = [gpu for gpu in gpus if is_v100(gpu)]
            ids = {g.uuid for g in gpus}
            for uuid in list(self.cards):
                if uuid not in ids:
                    self.rows.remove(self.cards.pop(uuid))
                    self.pending.pop(uuid, None)
                    if uuid in self.timers:
                        GLib.source_remove(self.timers.pop(uuid))
            for gpu in gpus:
                if gpu.uuid not in self.cards:
                    self.cards[gpu.uuid] = Card(gpu, self.changed, self.clock_changed)

                card = self.cards[gpu.uuid]
                card.scale.set_sensitive(gpu.adjustable)
                card.update(gpu, gpu.uuid in self.pending or gpu.uuid in self.busy or gpu.uuid in self.clock_pending or gpu.uuid in self.clock_busy)
            self.layout_cards()
            self.rows.show_all()
            self.controls()
            if self.attempts == 1:
                self.message(f"{len(gpus)} V100 GPU(s) · Drag a slider to set its limit" if gpus else "No V100 GPUs detected")
            if self.restore_on_start and gpus:
                self.restore_on_start = False
                self.load()
        if self.restore_on_start and self.attempts >= 15:
            self.restore_on_start = False
            self.message("Startup restore failed: NVIDIA GPUs did not become available. Use LOAD to retry.")
        return False

    def changed(self, uuid, watts):
        targets = self.cards.items() if self.link_check.get_active() else [(uuid, self.cards[uuid])]
        for target, card in targets:
            if not card.gpu.adjustable:
                continue
            value = snap_limit(card.gpu, watts)
            card.set_value(value)
            self.pending[target] = value
            if target in self.timers:
                GLib.source_remove(self.timers.pop(target))
            self.timers[target] = GLib.timeout_add(400, self.dispatch, target)
        self.controls()

    def toggle_link(self, checkbox):
        try:
            self.settings.save_ui(link_sliders=checkbox.get_active())
        except (OSError, ValueError) as exc:
            self.message(f"Could not save slider preference: {exc}")

    def layout_cards(self):
        ids = tuple(self.cards)
        if ids == self.layout_ids:
            return
        self.layout_ids = ids
        for child in self.rows.get_children():
            self.rows.remove(child)
        for index, card in enumerate(self.cards.values()):
            self.rows.attach(card, index // 4, index % 4, 1, 1)
        if not self.saved_size:
            width, height = self.base_size()
            screen = self.window.get_screen()
            self.window.resize(min(width, screen.get_width() - 80), min(height, screen.get_height() - 100))
        self.apply_scale(*self.window.get_size())

    def base_size(self):
        return (530 if len(self.cards) <= 4 else 1050, 280 + 175 * min(4, max(1, len(self.cards))))

    def apply_scale(self, width, height):
        base_w, base_h = self.base_size()
        factor = max(0.7, min(2.5, width / base_w, height / base_h))
        self.ui_scale = factor
        css = re.sub(rb"(\d+)px", lambda m: str(max(1, round(int(m[1]) * factor))).encode() + b"px", CSS)
        css += f"\nwindow {{ font-size: {round(13 * factor)}px; }}".encode()
        self.provider.load_from_data(css)
        gap = round(10 * factor)
        self.outer.set_spacing(gap)
        self.header.set_spacing(gap)
        self.buttons.set_spacing(gap)
        self.rows.set_row_spacing(gap)
        self.rows.set_column_spacing(gap)
        for card in self.cards.values():
            card.set_spacing(round(6 * factor))
            card.row.set_spacing(round(12 * factor))
            card.draw.set_size_request(round(75 * factor), -1)
            card.temp.set_size_request(round(60 * factor), -1)
            card.scale.set_size_request(round(220 * factor), -1)

    def resized(self, _window, event):
        size = [event.width, event.height]
        if not self.cards or not self.window.get_visible() or size == self.last_size:
            return False
        self.last_size = size
        self.apply_scale(*size)
        if self.resize_source:
            GLib.source_remove(self.resize_source)
        self.resize_source = GLib.timeout_add(500, self.persist_size)
        return False

    def persist_size(self):
        self.resize_source = None
        if self.last_size:
            try:
                self.settings.save_ui(window_size=self.last_size)
                self.saved_size = self.last_size
            except (OSError, ValueError) as exc:
                self.message(f"Could not save window size: {exc}")
        return False

    def clock_changed(self, uuid, value):
        targets = self.cards.items() if self.link_check.get_active() else [(uuid, self.cards[uuid])]
        for target, card in targets:
            if card.gpu.clock_options:
                selected = None if value is None else min(card.gpu.clock_options, key=lambda x: abs(x - value))
                self.queue_clock(target, selected)

    def queue_clock(self, uuid, value):
        self.cards[uuid].set_clock_value(value)
        self.clock_pending[uuid] = value
        if uuid in self.clock_timers:
            GLib.source_remove(self.clock_timers.pop(uuid))
        self.clock_timers[uuid] = GLib.timeout_add(400, self.dispatch_clock, uuid)
        self.controls()

    def reset_clocks(self):
        for uuid, card in self.cards.items():
            if card.gpu.clock_options:
                self.queue_clock(uuid, None)

    def dispatch_clock(self, uuid):
        self.clock_timers.pop(uuid, None)
        if uuid in self.clock_busy or uuid not in self.clock_pending:
            return False
        value = self.clock_pending.pop(uuid)
        if uuid not in self.cards:
            return False
        self.clock_busy.add(uuid)
        self.controls()
        self.apply_pool.submit(self.clock_worker, uuid, value)
        return False

    def clock_worker(self, uuid, value):
        try:
            apply_clock(uuid, value)
            error = None
        except Exception as exc:
            error = str(exc)
        GLib.idle_add(self.clock_finished, uuid, value, error)

    def clock_finished(self, uuid, value, error):
        if self.closed:
            return False
        self.clock_busy.discard(uuid)
        if error:
            self.load_errors.append(error)
            self.message(f"Clock change failed: {error}")
            if uuid in self.cards and uuid not in self.clock_pending:
                self.cards[uuid].set_clock_value(self.clock_applied.get(uuid), uuid in self.clock_applied)
        else:
            self.clock_applied[uuid] = value
            self.message("GPU clock: Auto · SAVE to remember" if value is None else f"GPU clock limit: {value} MHz · SAVE to remember")
        if uuid in self.clock_pending:
            if uuid in self.clock_timers:
                GLib.source_remove(self.clock_timers.pop(uuid))
            self.dispatch_clock(uuid)
        self.finish_loading()
        self.controls()
        self.poll()
        return False

    def dispatch(self, uuid):
        self.timers.pop(uuid, None)
        if uuid in self.busy or uuid not in self.pending or uuid not in self.cards:
            return False
        watts = self.pending.pop(uuid)
        try:
            self.cards[uuid].gpu.validate(watts)
        except ValueError as exc:
            self.message(str(exc))
            self.controls()
            return False
        self.busy.add(uuid)
        self.revision += 1
        self.message(f"Applying {watts:g} W to {self.cards[uuid].gpu.name}…")
        self.controls()
        self.apply_pool.submit(self.apply_worker, uuid, watts)
        return False

    def apply_worker(self, uuid, watts):
        try:
            actual, error = apply_limit(uuid, watts), None
        except Exception as exc:
            actual, error = None, str(exc)
        GLib.idle_add(self.applied, uuid, actual, error)

    def applied(self, uuid, actual, error):
        if self.closed:
            return False
        self.busy.discard(uuid)
        self.revision += 1
        if error:
            self.load_errors.append(error)
            self.message(f"Could not apply limit: {error}")
            if uuid in self.cards and uuid not in self.pending:
                self.cards[uuid].set_value(self.cards[uuid].gpu.limit)
        elif uuid in self.cards:
            if uuid not in self.pending:
                self.cards[uuid].set_value(actual)
            self.message(f"Applied {actual:g} W · SAVE to remember these limits")
        if uuid in self.pending:
            if uuid in self.timers:
                GLib.source_remove(self.timers.pop(uuid))
            self.dispatch(uuid)
        self.finish_loading()
        self.controls()
        self.poll()
        return False

    def finish_loading(self):
        if self.loading and not (self.busy or self.pending or self.clock_busy or self.clock_pending):
            self.message("; ".join(self.load_errors) if self.load_errors else "Saved power and clock limits applied")
            self.loading = False

    def save(self):
        try:
            limits = {u: c.gpu.validate(c.scale.get_value()) for u, c in self.cards.items() if c.gpu.adjustable}
            self.settings.save_limits(limits, self.clock_applied)
            self.message("Limits saved · " + ("Will apply at login" if self.start_check.get_active() else "Autostart is off"))
        except (OSError, ValueError) as exc:
            self.message(f"Could not save: {exc}")

    def load(self):
        try:
            profile = self.settings.read()
            saved = profile["limits"]
            targets, errors = load_targets(self.detected, saved)
        except (OSError, ValueError) as exc:
            self.message(f"Could not load: {exc}")
            return
        for uuid, value in profile.get("clocks", {}).items():
            if uuid not in self.cards:
                continue
            card = self.cards[uuid]
            if not card.gpu.clock_options or (value is not None and value not in card.gpu.clock_options):
                errors.append(f"{card.gpu.name}: saved clock unavailable")
                continue
            self.queue_clock(uuid, value)
        if not targets and not self.clock_pending:
            self.message("; ".join(errors) if errors else "No saved limits yet. Choose limits and press SAVE.")
            return
        self.loading = True
        self.load_errors = errors
        for uuid, watts in targets.items():
            self.cards[uuid].set_value(watts)
            self.pending[uuid] = watts
        for uuid in targets:
            self.dispatch(uuid)

    def toggle_startup(self, checkbox):
        enabled = checkbox.get_active()
        try:
            self.settings.set_startup(enabled)
            self.message("Autostart enabled · SAVE the limits you want restored" if enabled else
                         "Autostart disabled · Current GPU limits are unchanged")
        except (OSError, ValueError) as exc:
            checkbox.handler_block_by_func(self.toggle_startup)
            checkbox.set_active(not enabled)
            checkbox.handler_unblock_by_func(self.toggle_startup)
            self.message(f"Could not change autostart: {exc}")


def main():
    parser = argparse.ArgumentParser(description="NVIDIA power controls")
    parser.add_argument("--startup", action="store_true", help="Restore saved limits if autostart is enabled")
    args = parser.parse_args()
    GLib.set_prgname("v100set")
    GLib.set_application_name("v100set")
    Gtk.Window.set_default_icon_name("v100set")
    app = App(args.startup)
    try:
        return app.run([])
    finally:
        app.poll_pool.shutdown(wait=False, cancel_futures=True)
        app.apply_pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    raise SystemExit(main())
