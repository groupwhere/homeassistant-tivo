"""
Support for the Tivo receivers.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.tivo/
"""
import voluptuous as vol
import requests

from datetime import timedelta
import logging
import socket
import sys
import time
import os.path

from homeassistant import util
from homeassistant.components.media_player import (
    MEDIA_TYPE_TVSHOW, MEDIA_TYPE_VIDEO, SUPPORT_PAUSE, SUPPORT_PLAY_MEDIA,
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_STOP, PLATFORM_SCHEMA,
    SUPPORT_NEXT_TRACK, SUPPORT_PREVIOUS_TRACK, SUPPORT_PLAY, MediaPlayerDevice)
from homeassistant.const import (
    CONF_DEVICE, CONF_HOST, CONF_NAME, STATE_OFF, STATE_PLAYING, CONF_PORT)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import track_utc_time_change
from homeassistant.util.json import load_json, save_json

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'Tivo Receiver'
DEFAULT_PORT = 31339
DEFAULT_DEVICE = '0'

MIN_TIME_BETWEEN_SCANS = timedelta(seconds=10)
MIN_TIME_BETWEEN_FORCED_SCANS = timedelta(seconds=1)

SUPPORT_TIVO = SUPPORT_PAUSE |\
    SUPPORT_PLAY_MEDIA | SUPPORT_STOP | SUPPORT_NEXT_TRACK |\
    SUPPORT_TURN_ON | SUPPORT_TURN_OFF |\
    SUPPORT_PREVIOUS_TRACK | SUPPORT_PLAY

DATA_TIVO = "data_tivo"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_DEVICE, default=DEFAULT_DEVICE): cv.string,
})

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Tivo platform."""
    known_devices = hass.data.get(DATA_TIVO)
    if not known_devices:
        known_devices = []
    hosts = []

    if CONF_HOST in config:
        hosts.append([
            config.get(CONF_NAME),
            config.get(CONF_HOST),
            config.get(CONF_PORT),
            config.get(CONF_DEVICE)
        ])

    # Discovery not tested and likely not working
    elif discovery_info:
        host = discovery_info.get('host')
        name = 'Tivo_' + discovery_info.get('serial', '')

        # attempt to discover additional Tivo units
        try:
            resp = requests.get(
                'http://%s:%d/info/getLocations' % (host, DEFAULT_PORT)).json()
            if "locations" in resp:
                for loc in resp["locations"]:
                    if("locationName" in loc and "clientAddr" in loc
                       and loc["clientAddr"] not in known_devices):
                        hosts.append([str.title(loc["locationName"]), host,
                                      DEFAULT_PORT, loc["clientAddr"]])

        except requests.exceptions.RequestException:
            # bail out and just go forward with uPnP data
            if DEFAULT_DEVICE not in known_devices:
                hosts.append([name, host, DEFAULT_PORT, DEFAULT_DEVICE])

    tivos = []

    for host in hosts:
        tivos.append(TivoDevice(*host))
        known_devices.append(host[-1])

    add_devices(tivos)
    hass.data[DATA_TIVO] = known_devices

    track_utc_time_change(hass, lambda now: update_status(), second=30)

    @util.Throttle(MIN_TIME_BETWEEN_SCANS, MIN_TIME_BETWEEN_FORCED_SCANS)
    def update_status():
        for tivo in tivos:
            _LOGGER.warning("device: %s", tivo)
            tivo.get_status()

    return True

class TivoDevice(MediaPlayerDevice):
    """Representation of a Tivo receiver on the network."""

    def __init__(self, name, host, port, device):
        """Initialize the device."""
        self._name = name
        self._host = host
        self._port = port
        self._is_standby = False
        self._current = {}
        self._ignore = {}
        self.sock = None

        self.get_status()

    def connect(self, host, port):
        try:
            _LOGGER.warning("Connecting to Tivo...")
            self.sock = socket.socket()
            self.sock.settimeout(5)
            self.sock.connect((host, port))
#            self.sock.settimeout(None)
        except Exception:
            raise

    def disconnect(self):
        _LOGGER.warning("Disconnecting from Tivo...")
        self.sock.close()

    def get_status(self):
        _LOGGER.warning("Tivo get_status called...")
        data = self.send_code('','')
        """ e.g. CH_STATUS 0645 LOCAL """

        words = data.split()
        if words:
            try:
                _LOGGER.warning("Channel: %s", words[1])
                _LOGGER.warning("Status:  %s", words[2])

                if words[0] == "CH_STATUS":
                    self._current["channel"] = words[1]
                    self._current["title"]   = "Ch. " + words[1]
                    self._current["status"]  = words[2]
                    self._current["mode"]    = "TV"
            except IndexError:
                self._current["channel"] = "no channel"
                self._current["title"]   = "no title"
                self._current["status"]  = "no status"
                self._current["mode"]    = "none"
                _LOGGER.warning("Tivo did not respond correctly...")
 
    def send_code(self, code, cmdtype="IRCODE", extra=0, bufsize=1024):
        data = ""
        if extra:
            code = code + " " + extra
            # can be IRCODE, KEYBOARD, or TELEPORT.  Usually it's IRCODE but we might switch to KEYBOARD since it can do more.

        try:
            self.connect(self._host, self._port)
            if code:
                tosend = cmdtype + " " + code + "\r"
            else:
                tosend = ""

            _LOGGER.warning("Sending request: '%s'", tosend)

            self.sock.sendall(tosend.encode())
            data = self.sock.recv(bufsize);
            time.sleep(0.1)
            _LOGGER.warning("Received response: '%s'", data)

            self.disconnect()
            return data.decode()
        except Exception:
            raise

    def channel_scan(self):
        for i in range(1, self._channel_max):
            res = self.send_code('SETCH', 'IRCODE', str(i))
            words = res.split()
            if words[0] == 'INVALID':
                self._ignore.append(str(i))

    # MediaPlayerDevice properties and methods
    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        if self._is_standby:
            return STATE_OFF
        # Haven't determined a way to see if the content is paused
        return STATE_PLAYING

    @property
    def show_live(self):
        data = ""
        """Live TV. """
        """ Any client wishing to set a channel must wait for """
        """ LIVETV_READY before issuing a SETCH or FORCECH command. """
        data = self.send_code('LIVETV', 'TELEPORT')
        self._current["mode"] = "TV"
        return data.decode()

    @property
    def show_guide(self):
        data = ""
        """Guide."""
        """ Also returns status as with NOWPLAYING, e.g. CH_STATUS 0613 LOCAL """
        data = self.send_code('GUIDE', 'TELEPORT')
        self._current["mode"] = "GUIDE"
        return data.decode()

    @property
    def show_tivo(self):
        data = ""
        """Tivo menu."""
        self.send_code('TIVO', 'TELEPORT')
        self._current["mode"] = "MENU"
        return data.decode()

    @property
    def show_now(self):
        data = b""
        """Now playing."""
        data = self.send_code('NOWPLAYING', 'TELEPORT')
        self._current["mode"] = "NOWPLAYING"
        return data.decode()

    def channel_set(self, channel):
        """Channel set."""
        data = self.show_live()
        if(data == "LIVETV READY"):
            self.send_code('SETCH', 'IRCODE', channel)

    def media_ch_up(self):
        """Channel up."""
        if self._current["mode"] == "TV":
            data = self.send_code('CHANNELUP')
            words = data.split()
            self._current["channel"] = words[1]
            self._current["title"]   = "Ch. " + words[1]
            self._current["status"]  = words[2]

    def media_ch_dn(self):
        """Channel down."""
        if self._current["mode"] == "TV":
            data = self.send_code('CHANNELDOWN')
            words = data.split()
            self._current["channel"] = words[1]
            self._current["title"]   = "Ch. " + words[1]
            self._current["status"]  = words[2]

    @property
    def media_content_id(self):
        """Return the content ID of current playing media."""
        if self._is_standby:
            return None

        return self._current["status"]

    @property
    def media_duration(self):
        """Return the duration of current playing media in seconds."""
        if self._is_standby:
            return None

        return ""

    @property
    def media_title(self):
        """Return the title of current playing media."""
        if self._is_standby:
            return None
        return self._current['title']

    @property
    def media_series_title(self):
        """Return the title of current episode of TV show."""
        if self._is_standby:
            return None
        elif 'episodeTitle' in self._current:
            return self._current['episodeTitle']
        return ""

    @property
    def support_ch_dn(self):
        """Boolean if channel down command supported."""
        return bool(self.supported_features & SUPPORT_CHANNEL_STEP)

    @property
    def support_ch_up(self):
        """Boolean if channel up command supported."""
        return bool(self.supported_features & SUPPORT_CHANNEL_STEP)

#    @property
#    def support_ch_buttons(self):
#        """Boolean if channel buttons supported."""
#        return bool(self.supported_features & SUPPORT_CH_BUTTONS)

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_TIVO

    @property
    def media_content_type(self):
        """Return the content type of current playing media."""
        if self._is_standby:
            return

        if 'episodeTitle' in self._current:
            return MEDIA_TYPE_TVSHOW
        return MEDIA_TYPE_VIDEO

    @property
    def media_channel(self):
        """Return the channel current playing media."""
        if self._is_standby:
            return None

        return "{} ({})".format(
            self._current['status'], self._current['channel'])

    @property
    def turn_on(self):
        """Turn on the receiver. """
        if self._is_standby:
            self.send_code('STANDBY','IRCODE')
            self._is_standby = False

    @property
    def turn_off(self):
        """Turn off the receiver. """
        if self._is_standby == False:
            self.send_code('STANDBY','IRCODE')
            self.send_code('STANDBY','IRCODE')
            self._is_standby = True

    @property
    def media_play(self):
        """Send play command."""
        if self._is_standby:
            return

        self.send_code('PLAY')

    @property
    def media_pause(self):
        """Send pause command."""
        if self._is_standby:
            return None

        self.send_code('PAUSE', 'IRCODE', 0, 0)
        words = data.split()
        return words[2]

    @property
    def media_stop(self):
        """Send stop command. """
        if self._is_standby:
            return None

        if self._current["mode"] == "TV":
            return "INTV"

        data = self.send_code('STOP', 'IRCODE', 0, 0)
        words = data.split()
        return words[2]

    @property
    def media_record(self):
        """ Start recording the current program """
        if self._is_standby:
             return

        self.send_code('RECORD', 'IRCODE')

    def media_previous_track(self):
        """Send rewind command."""
        if self._is_standby:
            return

        if self._current["mode"] in ("TV", "none"):
            self.media_ch_dn()
        else:
            self.send_code('REVERSE', 'IRCODE', 0, 0)

    def media_next_track(self):
        """Send fast forward command."""
        if self._is_standby:
            return

        if self._current["mode"] in ("TV", "none"):
            self.media_ch_up()
        else:
            self.send_code('FORWARD', 'IRCODE', 0, 0)

