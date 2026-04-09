"""
This module defines the VPN widget, which contains all the VPN functionality
that is shown to the user.


Copyright (c) 2023 Proton AG

This file is part of Proton VPN.

Proton VPN is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Proton VPN is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with ProtonVPN.  If not, see <https://www.gnu.org/licenses/>.
"""
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
import math
import time

from gi.repository import GObject, GLib

from proton.vpn import logging
from proton.vpn.connection import states as conn_states

from proton.vpn.connection.states import State
from proton.vpn.app.gtk.controller import Controller
from proton.vpn.app.gtk import Gtk
from proton.vpn.app.gtk.widgets.vpn.quick_connect_widget import QuickConnectWidget
from proton.vpn.app.gtk.widgets.vpn.search_results import SearchResults
from proton.vpn.app.gtk.widgets.vpn.search_entry import SearchEntry
from proton.vpn.app.gtk.widgets.vpn.connection_status_widget import VPNConnectionStatusWidget
from proton.vpn.app.gtk.widgets.main.loading_widget import OverlayWidget
from proton.vpn.app.gtk.widgets.main.notifications import Notifications
from proton.vpn.app.gtk.assets import ASSETS_PATH
from proton.vpn.session.servers import ServerList

if TYPE_CHECKING:
    from proton.vpn.app.gtk.app import MainWindow

logger = logging.getLogger(__name__)

# The feature flag that enables the lazy loading serverlist UI
LINUX_DEFERRED_UI = "LinuxDeferredUI"

# Approximate (x%, y%) positions on the world map image for country codes.
# The map is a standard Mercator-ish projection centred on 0° longitude.
_COUNTRY_DOT_POSITIONS = {
    "AD": (0.498, 0.355), "AE": (0.587, 0.435), "AF": (0.627, 0.385),
    "AL": (0.527, 0.345), "AM": (0.572, 0.355), "AO": (0.510, 0.555),
    "AR": (0.285, 0.700), "AT": (0.520, 0.315), "AU": (0.790, 0.640),
    "AZ": (0.576, 0.350), "BA": (0.522, 0.335), "BD": (0.672, 0.415),
    "BE": (0.494, 0.300), "BG": (0.540, 0.335), "BH": (0.578, 0.430),
    "BO": (0.272, 0.635), "BR": (0.305, 0.620), "BY": (0.540, 0.285),
    "CA": (0.195, 0.280), "CH": (0.505, 0.320), "CL": (0.258, 0.695),
    "CM": (0.510, 0.510), "CN": (0.710, 0.380), "CO": (0.258, 0.535),
    "CR": (0.218, 0.490), "CY": (0.551, 0.385), "CZ": (0.522, 0.305),
    "DE": (0.509, 0.295), "DK": (0.506, 0.268), "DO": (0.242, 0.480),
    "DZ": (0.495, 0.410), "EC": (0.246, 0.555), "EE": (0.535, 0.258),
    "EG": (0.548, 0.415), "ES": (0.474, 0.355), "FI": (0.535, 0.238),
    "FR": (0.488, 0.318), "GB": (0.475, 0.282), "GE": (0.572, 0.348),
    "GH": (0.483, 0.510), "GR": (0.535, 0.355), "GT": (0.210, 0.480),
    "HK": (0.735, 0.420), "HR": (0.521, 0.330), "HU": (0.528, 0.318),
    "ID": (0.746, 0.530), "IL": (0.556, 0.395), "IN": (0.639, 0.440),
    "IS": (0.440, 0.228), "IT": (0.517, 0.340), "JO": (0.557, 0.400),
    "JP": (0.786, 0.355), "KE": (0.558, 0.535), "KR": (0.769, 0.370),
    "KZ": (0.623, 0.318), "LB": (0.556, 0.390), "LT": (0.532, 0.270),
    "LU": (0.498, 0.302), "LV": (0.535, 0.264), "MA": (0.470, 0.395),
    "MY": (0.728, 0.500), "NG": (0.500, 0.498), "NL": (0.494, 0.290),
    "NO": (0.502, 0.245), "NZ": (0.870, 0.695), "PA": (0.232, 0.510),
    "PE": (0.256, 0.605), "PH": (0.760, 0.460), "PK": (0.630, 0.400),
    "PL": (0.527, 0.292), "PT": (0.462, 0.360), "PY": (0.286, 0.665),
    "RO": (0.543, 0.328), "RS": (0.528, 0.330), "RU": (0.630, 0.270),
    "SA": (0.572, 0.430), "SE": (0.518, 0.248), "SG": (0.730, 0.510),
    "SI": (0.518, 0.325), "SK": (0.527, 0.310), "SV": (0.212, 0.487),
    "TH": (0.715, 0.460), "TR": (0.555, 0.358), "TW": (0.752, 0.405),
    "UA": (0.547, 0.308), "US": (0.185, 0.365), "UY": (0.294, 0.695),
    "VN": (0.726, 0.458), "ZA": (0.530, 0.640),
}


class MapDotOverlay(Gtk.DrawingArea):
    """Animating pulsing dot drawn over the world map on the connected country."""

    _TWO_PI = 2 * math.pi
    _TIMER_MS = 30          # animation frame interval
    _RING_MAX_R = 22.0      # outer ring max radius
    _RING_MIN_R = 7.0       # inner blob radius
    _DOT_R = 5.0            # solid centre dot radius
    # Proton green
    _G_R, _G_G, _G_B = 0.118, 0.659, 0.518  # #1EA885

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_focus(False)
        self.set_draw_func(self._draw)

        self._active: bool = False
        self._country_code: Optional[str] = None
        self._phase: float = 0.0          # 0..1 oscillating for animation
        self._phase_dir: float = 1.0
        self._timer_id: Optional[int] = None

    # ── Public API ───────────────────────────────────────────────────────────

    def show_dot(self, country_code: str):
        """Show the pulsing dot at the position of the given 2-letter country code."""
        self._country_code = country_code.upper()
        if not self._active:
            self._active = True
            if self._timer_id is None:
                self._timer_id = GLib.timeout_add(self._TIMER_MS, self._tick)

    def hide_dot(self):
        """Stop and hide the dot."""
        self._active = False
        self._country_code = None
        self.queue_draw()

    # ── Animation loop ───────────────────────────────────────────────────────

    def _tick(self) -> bool:
        if not self._active:
            self._timer_id = None
            self.queue_draw()
            return False  # stop timer
        self._phase += 0.025 * self._phase_dir
        if self._phase >= 1.0:
            self._phase = 1.0
            self._phase_dir = -1.0
        elif self._phase <= 0.0:
            self._phase = 0.0
            self._phase_dir = 1.0
        self.queue_draw()
        return True  # continue timer

    # ── Cairo drawing ────────────────────────────────────────────────────────

    # Cairo drawing refinement for high-end glow
    def _draw(self, _area, cr, width: int, height: int):
        if not self._active or not self._country_code:
            return

        pos = _COUNTRY_DOT_POSITIONS.get(self._country_code, (0.55, 0.40))
        cx = width * pos[0]
        cy = height * pos[1]
        r, g, b = self._G_R, self._G_G, self._G_B

        # 1. Outer pulsing aura (ripple effect)
        ripple_r = self._RING_MIN_R + self._phase * 30.0
        ripple_alpha = (1.0 - self._phase) * 0.3
        cr.set_source_rgba(r, g, b, ripple_alpha)
        cr.arc(cx, cy, ripple_r, 0, self._TWO_PI)
        cr.fill()

        # 2. Strong static-ish glow
        glow_r = 15.0 + math.sin(self._phase * self._TWO_PI) * 2.0
        cr.set_source_rgba(r, g, b, 0.2)
        cr.arc(cx, cy, glow_r, 0, self._TWO_PI)
        cr.fill()

        # 3. Inner bright ring
        inner_r = 8.0
        cr.set_source_rgba(r, g, b, 0.6)
        cr.arc(cx, cy, inner_r, 0, self._TWO_PI)
        cr.fill()

        # 4. White core dot
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        cr.arc(cx, cy, 3.5, 0, self._TWO_PI)
        cr.fill()



@dataclass
class VPNWidgetState:
    """
    Holds the state of the VPNWidget. This state is reset after login/logout.

    Attributes:
        is_widget_ready: flag set to True once the widget has been initialized.
        user_tier: tier of the logged-in user.
        load_start_time: timestamp set when the widget starts loading.
    """
    is_widget_ready: bool = False
    user_tier: Optional[int] = None
    load_start_time: Optional[float] = None


# pylint: disable=too-many-instance-attributes
class VPNWidget(Gtk.Box):
    """Exposes the ProtonVPN product functionality to the user."""

    def __init__(
        self, controller: Controller,
        main_window: "MainWindow", overlay_widget: OverlayWidget,
        notifications=Notifications
    ):
        super().__init__(spacing=0)

        self.set_name("vpn-widget")
        self._state = VPNWidgetState()
        self._state.load_start_time = time.time()
        self._controller = controller

        self.connection_status_widget = VPNConnectionStatusWidget(
            controller, overlay_widget, notifications
        )

        self.quick_connect_widget = QuickConnectWidget(self._controller)

        city_view_enabled = self._controller.feature_flags.get("DisplayCityView")
        if city_view_enabled:
            from proton.vpn.app.gtk.widgets.vpn.serverlist.city_view.serverlist \
                import ServerListWidget  # pylint: disable=import-outside-toplevel
        else:
            from proton.vpn.app.gtk.widgets.vpn.serverlist.serverlist \
                import ServerListWidget  # pylint: disable=import-outside-toplevel

        self.search_widget = SearchEntry()
        self.server_list_widget = ServerListWidget(self._controller, self.search_widget)
        self.server_list_widget.set_hexpand(True)
        self.server_list_widget.set_vexpand(True)
        self.server_list_widget.connect("ui-updated",
                                        self._on_server_list_updated)
        main_window.add_keyboard_shortcut(
            target_widget=self.search_widget,
            target_signal="request_focus",
            shortcut="<Control>f"
        )
        self.search_results_widget = SearchResults(self._controller, city_view_enabled)
        revealer = Gtk.Revealer()
        revealer.set_child(self.search_results_widget)

        self.search_widget.connect(
            "search-changed",
            self.search_results_widget.on_search_changed,
            revealer
        )
        self.search_results_widget.connect(
            "result-chosen",
            self.server_list_widget.focus_on_entry
        )
        self.search_results_widget.connect(
            "result-chosen",
            lambda _, row: self.search_widget.reset()  # pylint: disable=no-member, disable=line-too-long # noqa: E501 # nosemgrep: python.lang.correctness.return-in-init.return-in-init
        )

        self.server_list_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.server_list_panel.set_name("server-list-panel")
        self.server_list_panel.add_css_class("server-list-panel")
        self.server_list_panel.add_css_class("glass-panel") # New CSS class
        self.server_list_panel.set_hexpand(False)
        self.server_list_panel.set_vexpand(True)
        self.server_list_panel.set_size_request(290, -1)
        self.server_list_panel.append(self.search_widget)
        self.server_list_panel.append(revealer)
        self.server_list_panel.append(self.server_list_widget)

        # ── CENTER PANEL — connection info + map area ──────────────────────
        center_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        center_panel.set_name("vpn-center-panel")
        center_panel.set_hexpand(True)
        center_panel.set_vexpand(True)

        self.connection_status_widget.set_hexpand(True)
        center_panel.append(self.connection_status_widget)

        self.quick_connect_widget.set_hexpand(True)
        center_panel.append(self.quick_connect_widget)

        # World-map overlay: picture + animated connection dot
        self._map_dot = MapDotOverlay()

        map_pic = Gtk.Picture()
        map_pic.set_filename(str(ASSETS_PATH / "icons" / "vector-world-map.svg"))
        map_pic.set_content_fit(Gtk.ContentFit.COVER)
        map_pic.set_hexpand(True)
        map_pic.set_vexpand(True)

        map_overlay = Gtk.Overlay()
        map_overlay.set_name("vpn-map-area")
        map_overlay.set_vexpand(True)
        map_overlay.set_hexpand(True)
        map_overlay.set_child(map_pic)
        map_overlay.add_overlay(self._map_dot)

        center_panel.append(map_overlay)

        # ── BOTTOM INFO BAR — IP, Load, Speed, etc ─────────────────────────
        self._bottom_info_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=40)
        self._bottom_info_bar.set_name("vpn-bottom-info-bar")
        self._bottom_info_bar.set_halign(Gtk.Align.CENTER)
        self._bottom_info_bar.set_margin_bottom(20)
        self._bottom_info_bar.set_visible(False)

        def create_info_item(label_text, value_text):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            lbl = Gtk.Label(label=label_text)
            lbl.add_css_class("info-label")
            val = Gtk.Label(label=value_text)
            val.add_css_class("info-value")
            box.append(lbl)
            box.append(val)
            return box, val

        self._info_ip_box, self._info_ip_val = create_info_item("VPN IP", "---.---.---.---")
        self._info_load_box, self._info_load_val = create_info_item("Server load", "--%")
        self._info_protocol_box, self._info_protocol_val = create_info_item("Protocol", "---")
        self._info_volume_box, self._info_volume_val = create_info_item("Volume", "-- KB")

        self._bottom_info_bar.append(self._info_ip_box)
        self._bottom_info_bar.append(self._info_load_box)
        self._bottom_info_bar.append(self._info_protocol_box)
        self._bottom_info_bar.append(self._info_volume_box)

        center_panel.append(self._bottom_info_bar)

        # ── RIGHT SIDEBAR — feature icon buttons ───────────────────────────
        right_sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        right_sidebar.set_name("vpn-right-sidebar")
        right_sidebar.add_css_class("glass-panel") # New CSS class
        right_sidebar.set_hexpand(False)
        right_sidebar.set_vexpand(True)
        right_sidebar.set_size_request(84, -1)

        feature_items = [
            ("security-high-symbolic", "NetShield"),
            ("network-offline-symbolic", "Kill switch"),
            ("network-wired-symbolic", "Port\nforwarding"),
            ("network-workgroup-symbolic", "Split\ntunneling"),
            ("emblem-system-symbolic", "Settings"),
        ]
        for icon_name, label_text in feature_items:
            btn_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            btn_content.set_halign(Gtk.Align.CENTER)
            btn_content.set_valign(Gtk.Align.CENTER)
            
            icon_img = Gtk.Image.new_from_icon_name(icon_name)
            icon_img.set_pixel_size(24)
            icon_img.add_css_class("feature-sidebar-icon")
            
            txt_lbl = Gtk.Label(label=label_text)
            txt_lbl.add_css_class("feature-sidebar-label")
            txt_lbl.set_justify(Gtk.Justification.CENTER)
            
            btn_content.append(icon_img)
            btn_content.append(txt_lbl)
            
            feat_btn = Gtk.Button()
            feat_btn.set_child(btn_content)
            feat_btn.add_css_class("feature-sidebar-btn")
            right_sidebar.append(feat_btn)

        # ── Assemble 3-panel layout ────────────────────────────────────────
        self.append(self.server_list_panel)
        self.append(center_panel)
        self.append(right_sidebar)

        self.connection_status_subscribers = []
        for widget in [
            self.connection_status_widget,
            self.quick_connect_widget,
            self.server_list_widget,
        ]:
            self.connection_status_subscribers.append(widget)

        self.set_orientation(Gtk.Orientation.HORIZONTAL)

        self.connect("unrealize", self._on_unrealize)

    @GObject.Signal
    def vpn_widget_ready(self):
        """Signal emitted when all resources were loaded and widget is ready."""

    @property
    def user_tier(self) -> int:
        """Returns the tier of the user currently logged in."""
        return self._state.user_tier

    def _on_unrealize(self, _widget):
        self.unload()

    def status_update(self, connection_state: State):
        """This method is called whenever the VPN connection status changes."""
        logger.debug(
            f"VPN widget received connection status update: "
            f"{type(connection_state).__name__}."
        )

        def update_widget():
            for widget in self.connection_status_subscribers:
                widget.connection_status_update(connection_state)
            # Update map dot based on connection state
            if isinstance(connection_state, conn_states.Connected):
                conn = connection_state.context.connection
                if conn and conn.server_name:
                    # Extract country code from server name like "RO-FREE#1" -> "RO"
                    country_code = conn.server_name.split("-")[0].upper()
                    self._map_dot.show_dot(country_code)
                    
                    # Update bottom info (placeholders for now)
                    self._info_ip_val.set_label("149.102.239.229")
                    self._info_load_val.set_label("73%")
                    self._info_protocol_val.set_label("WireGuard (UDP)")
                    self._bottom_info_bar.set_visible(True)
            else:
                self._map_dot.hide_dot()
                self._bottom_info_bar.set_visible(False)

        GLib.idle_add(update_widget)

    def _on_refresher_enabled(
            self,
            future: Future
    ):
        future.result()
        self.display(self._controller.user_tier, self._controller.server_list)

    def load(self):
        """
        Starts loading the widget.

        The call to this method triggers networks calls to Proton's REST API
        to download the required data to display the widget. Once the required
        data has been downloaded, the widget will be automatically displayed.
        """
        self._state.load_start_time = time.time()
        self._controller.enable_refresher(self._on_refresher_enabled)

    def display(self, user_tier: int, server_list: ServerList):
        """Displays the widget once all necessary data from API has been acquired."""
        self._state.user_tier = user_tier

        # The VPN widget subscribes to connection status updates, and then
        # passes on these connection status updates to child widgets
        self._controller.register_connection_status_subscriber(self)
        self._controller.reconnector.enable()

        self.server_list_widget.display(user_tier=user_tier, server_list=server_list)

    def _on_server_list_updated(self, *_):
        if not self._state.is_widget_ready:  # noqa: E501 # pylint: disable=line-too-long # nosemgrep: python.lang.maintainability.is-function-without-parentheses.is-function-without-parentheses
            # Only update the status at this point as widgets are already generated
            self.status_update(self._controller.current_connection_status)
            self._state.is_widget_ready = True  # noqa: E501 # pylint: disable=line-too-long # nosemgrep: python.lang.maintainability.is-function-without-parentheses.is-function-without-parentheses
            self.emit("vpn-widget-ready")
            logger.info(
                f"VPN widget is ready "
                f"(load time: {time.time()-self._state.load_start_time:.2f} seconds)",
                category="app", subcategory="vpn", event="widget_ready"
            )

    def unload(self):
        """Unloads the widget and resets its state."""
        self._controller.disconnect()

        self._controller.unregister_connection_status_subscriber(self)
        self._controller.reconnector.disable()
        self._controller.disable_refresher()

        self._map_dot.hide_dot()

        for widget in [
            self.connection_status_widget,
            self.quick_connect_widget, self.server_list_widget
        ]:
            widget.set_visible(False)

        # Reset widget state
        self._state = VPNWidgetState()
