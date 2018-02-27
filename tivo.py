"""
Support for the Tivo receivers.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.tivo/
"""
import voluptuous as vol
import requests

import logging
import socket
import sys
import time
import os.path

from homeassistant.components.media_player import (
    MEDIA_TYPE_TVSHOW, MEDIA_TYPE_VIDEO, SUPPORT_PAUSE, SUPPORT_PLAY_MEDIA,
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_STOP, PLATFORM_SCHEMA,
    SUPPORT_NEXT_TRACK, SUPPORT_PREVIOUS_TRACK, SUPPORT_PLAY,
    MediaPlayerDevice)
from homeassistant.const import (
    CONF_DEVICE, CONF_HOST, CONF_NAME, STATE_OFF, STATE_PLAYING, CONF_PORT)
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DEFAULT_DEVICE = '0'
DEFAULT_NAME = 'Tivo Receiver'
DEFAULT_PORT = 31339

SUPPORT_TIVO = SUPPORT_PAUSE |\
    SUPPORT_PLAY_MEDIA | SUPPORT_STOP | SUPPORT_NEXT_TRACK |\
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
            config.get(CONF_NAME), config.get(CONF_HOST),
            config.get(CONF_PORT), config.get(CONF_DEVICE)
        ])

    elif discovery_info:
        host = discovery_info.get('host')
        name = 'Tivo_' + discovery_info.get('serial', '')

        # attempt to discover additional RVU units
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
        self.sock = None

        data = self.send_code('','')
        """ CH_STATUS 0645 LOCAL """

        words = data.split()
        _LOGGER.error("Channel:  %s", words[1])
        _LOGGER.error("Status:  %s", words[2])
        self._current["channel"] = words[1]
        self._current["status"]  = words[2]

    def connect(self, host, port):
        try:
            self.sock = socket.socket()
            self.sock.settimeout(5)
            self.sock.connect((host, port))
            self.sock.settimeout(None)
        except Exception:
            raise

    def disconnect(self):
        self.sock.close()

    def send_code(self, code, cmdtype="IRCODE", extra=0):
        data = ""
        if extra:
            code = code + " " + extra
        # can be IRCODE, KEYBOARD, or TELEPORT.  Usually it's IRCODE but we might switch to KEYBOARD since it can do more.
        if not self.sock:
            self.connect(self._host, self._port)
        try:
            tosend = cmdtype + " " + code + "\r"
            _LOGGER.error("Sending request: %s", tosend)

            self.sock.sendall(tosend.encode())
            data = self.sock.recv(1024);
            time.sleep(0.1)
            _LOGGER.error("Received Tivo data: %s", data)
            return data.decode()
        except Exception:
            raise

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    # MediaPlayerDevice properties and methods
    @property
    def state(self):
        """Return the state of the device."""
        if self._is_standby:
            return STATE_OFF
        # Haven't determined a way to see if the content is paused
        return STATE_PLAYING
        #return self.show_now()

    @property
    def show_live(self):
        data = ""
        """Live TV. """
        """ Any client wishing to set a channel must wait for """
        """ LIVETV_READY before issuing a SETCH or FORCECH command. """
        data = self.send_code('LIVETV', 'TELEPORT')
        return data.decode()

    @property
    def show_guide(self):
        data = ""
        """Guide."""
        """ Also returns status as with NOWPLAYING, e.g. CH_STATUS 0613 LOCAL """
        data = self.send_code('GUIDE', 'TELEPORT')
        return data.decode()

    @property
    def show_tivo(self):
        data = ""
        """Tivo menu."""
        self.send_code('TIVO', 'TELEPORT')
        return data.decode()

    @property
    def show_now(self):
        data = b""
        """Now playing."""
        """ This is how we find out current status """
        data = self.send_code('NOWPLAYING', 'TELEPORT')
        """ Ex: data = CH_STATUS 0613 LOCAL """
        return data.decode()

    @property
    def channel_set(self, channel):
        """Channel set."""
        data = self.show_live()
        if(data == "LIVETV READY"):
            self.send_code('SETCH', 'IRCODE', channel)

    @property
    def media_next_track(self):
        """Channel up."""
        self.send_code('CHANNELUP')

    @property
    def media_previous_track(self):
        """Channel down."""
        self.send_code('CHANNELDOWN')

    @property
    def media_content_id(self):
        """Return the content ID of current playing media."""
        if self._is_standby:
            return None
        #return self._current['programId']
        return ""

    @property
    def media_duration(self):
        """Return the duration of current playing media in seconds."""
#        if self._is_standby:
#            return None
#        return self._current['duration']

        return ""

    @property
    def media_title(self):
        """Return the title of current playing media."""
        if self._is_standby:
            return None
        return ""
        """ self._current['title'] """

    @property
    def media_series_title(self):
        """Return the title of current episode of TV show."""
        if self._is_standby:
            return None
#        elif 'episodeTitle' in self._current:
#            return self._current['episodeTitle']
        return ""

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_TIVO

    @property
    def media_content_type(self):
        """Return the content type of current playing media."""
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
        """Turn on the receiver.  NOPE"""
        #self.send_code('poweron')

    @property
    def turn_off(self):
        """Turn off the receiver. NOPE"""
        #self.send_code('poweroff')

    @property
    def media_play(self):
        """Send play command."""
        self.send_code('PLAY')

    @property
    def media_pause(self):
        """Send pause command."""
        self.send_code('PAUSE')

    @property
    def media_stop(self):
        """Send stop command. NOT VALID! """
        self.send_code('STOP')

    @property
    def media_previous_track(self):
        """Send rewind command."""
        self.send_code('REVERSE')

    @property
    def media_next_track(self):
        """Send fast forward command."""
        self.send_code('FORWARD')
