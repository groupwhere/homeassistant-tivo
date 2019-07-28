"""
Support for the Tivo receivers.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.tivo/
"""
import voluptuous as vol
import requests
import re
import asyncio
import zeroconf

from datetime import timedelta
#from pytz import timezone
import logging
import socket
import sys
import time
from calendar import timegm
import json
import urllib
from urllib.parse import urlencode
import os.path

from homeassistant import util
from homeassistant.components.media_player import (
        PLATFORM_SCHEMA, MediaPlayerDevice)
from homeassistant.components.media_player.const import (
    MEDIA_TYPE_TVSHOW, MEDIA_TYPE_VIDEO, SUPPORT_PAUSE, SUPPORT_PLAY_MEDIA,
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_STOP,
    SUPPORT_NEXT_TRACK, SUPPORT_PREVIOUS_TRACK, SUPPORT_PLAY)
from homeassistant.const import (
    CONF_DEVICE, CONF_HOST, CONF_NAME, STATE_OFF, STATE_STANDBY, STATE_PLAYING, CONF_PORT, CONF_USERNAME, CONF_PASSWORD)
import homeassistant.helpers.config_validation as cv
#from homeassistant.helpers.event import (track_utc_time_change, track_time_interval)
from homeassistant.helpers.event import track_time_interval
from homeassistant.util.json import load_json, save_json

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'Tivo Receiver'
DEFAULT_PORT = 31339
DEFAULT_DEVICE = '0'

CONF_ZAPUSER = 'zapuser'
CONF_ZAPPASS = 'zappass'
CONF_DEBUG   = 'debug'

SCAN_INTERVAL = timedelta(seconds=10)
ZAP_SCAN_INTERVAL = timedelta(seconds=300)

SUPPORT_TIVO = SUPPORT_PAUSE |\
    SUPPORT_PLAY_MEDIA | SUPPORT_STOP | SUPPORT_NEXT_TRACK |\
    SUPPORT_TURN_ON | SUPPORT_TURN_OFF |\
    SUPPORT_PREVIOUS_TRACK | SUPPORT_PLAY

DATA_TIVO = "data_tivo"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_HOST): cv.string,
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
    vol.Optional(CONF_DEVICE, default=DEFAULT_DEVICE): cv.string,
    vol.Optional(CONF_ZAPUSER, default=""): cv.string,
    vol.Optional(CONF_ZAPPASS, default=""): cv.string,
    vol.Optional(CONF_DEBUG, default=0): cv.string
})

def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Tivo platform."""
    known_devices = hass.data.get(DATA_TIVO)
    if not known_devices:
        known_devices = []
    hosts = []

    zapuser = config.get(CONF_ZAPUSER)
    zappass = config.get(CONF_ZAPPASS)
    zapclient = None
    debug = config.get(CONF_DEBUG)

    if zapuser and zappass:
        zapclient = Zap2ItClient(zapuser, zappass, debug)

    if CONF_HOST in config:
        hosts.append([
            config.get(CONF_NAME),
            config.get(CONF_HOST),
            config.get(CONF_PORT),
            config.get(CONF_DEVICE),
            zapclient,
            debug
        ])

    # Discovery not tested and likely not working
    else:
        zc_hosts = find_tivos_zc()

        if len(zc_hosts) != 0:
            # attempt to discover additional Tivo units
            device = 0
            for name, ip_addr in zc_hosts.items():
                hosts.append([name + " TiVo", ip_addr, DEFAULT_PORT, device, zapclient, debug])
                device = device + 1
        else:
            # bail out and just go forward with uPnP data
            if DEFAULT_DEVICE not in known_devices:
                hosts.append([name, host, DEFAULT_PORT, DEFAULT_DEVICE, zapclient, debug])

    tivos = []

    for host in hosts:
        tivos.append(TivoDevice(*host))
        known_devices.append(host[-1])

    add_devices(tivos)
    hass.data[DATA_TIVO] = known_devices

    def update_status(event_time):
        for tivo in tivos:
            if tivo.debug:
                _LOGGER.info("update_status: %s", tivo)
            tivo.get_status()

    def zap2it_update(event_time):
        zapclient.update()

    track_time_interval(hass, update_status, SCAN_INTERVAL)
    if zapclient:
        track_time_interval(hass, zap2it_update, ZAP_SCAN_INTERVAL)

    return True

#
# Taken from https://github.com/wmcbrine/tivoremote.git
#
def find_tivos_zc():
    """ Find TiVos on the LAN using Zeroconf. This is simpler and
        cleaner than the fake HTTP method, but slightly slower, and
        requires the Zeroconf module. (It's still much faster than
        waiting for beacons.)

    """

    class ZCListener:
        def __init__(self, names):
            self.names = names

        def remove_service(self, server, type, name):
            self.names.remove(name)

        def add_service(self, server, type, name):
            self.names.append(name)

    REMOTE = '_tivo-remote._tcp.local.'

    tivo_ports = {}
    tivo_swversions = {}

    tivos = {}
    tivos_rev = {}
    tivo_names = []

    # Get the names of TiVos offering network remote control
    try:
        serv = zeroconf.Zeroconf()
        browser = zeroconf.ServiceBrowser(serv, REMOTE, ZCListener(tivo_names))
    except:
        return tivos

    # Give them a second to respond
    time.sleep(1)

    # For proxied TiVos, remove the original
    for t in tivo_names[:]:
        if t.startswith('Proxy('):
            try:
                t = t.replace('.' + REMOTE, '')[6:-1] + '.' + REMOTE
                tivo_names.remove(t)
            except:
                pass

    # Now get the addresses -- this is the slow part
    swversion = re.compile('(\d*.\d*)').findall
    for t in tivo_names:
        s = serv.get_service_info(REMOTE, t)
        if s:
            name = t.replace('.' + REMOTE, '')
            address = socket.inet_ntoa(s.address)
            try:
                version = float(swversion(s.getProperties()['swversion'])[0])
            except:
                version = 0.0
            tivos[name] = address
            tivos_rev[address] = name
            tivo_ports[name] = s.port
            tivo_swversions[name] = version

    # For proxies with numeric names, remove the original
    for t in tivo_names:
        if t.startswith('Proxy('):
            address = t.replace('.' + REMOTE, '')[6:-1]
            if address in tivos_rev:
                tivos.pop(tivos_rev[address])

    serv.close()
    return tivos

class TivoDevice(MediaPlayerDevice):
    """Representation of a Tivo receiver on the network."""

    def __init__(self, name, host, port, device, zapclient, debug):
        """Initialize the device."""
        self._name = name
        self._host = host
        self._port = port

        self.zapclient = zapclient

        self._is_standby = False
        self._current = {}
        self._ignore = {}
        self.sock = None

        debug = bool(int(debug))
        self.debug = debug

        self.get_status()

    def connect(self, host, port):
        try:
            if self.debug:
                _LOGGER.info("Connecting to device...")
            self.sock = socket.socket()
            self.sock.settimeout(5)
            self.sock.connect((host, port))
        except Exception:
            raise

    def disconnect(self):
        if self.debug:
            _LOGGER.info("Disconnecting from device...")
        self.sock.close()

    def get_status(self):
        if self.debug:
            _LOGGER.info("get_status called...")
        data = self.send_code('','')
        """ e.g. CH_STATUS 0645 LOCAL """
        """ e.g. CH_STATUS 0645 RECORDING """

        words = data.split()
        self.set_status(words)

    def set_status(self, words):
        self._is_standby = True

        if not words:
            _LOGGER.debug("device did not respond correctly...")
            return

        self._current["channel"] = "no channel"
        self._current["title"]   = "no title"
        self._current["status"]  = "no status"
        self._current["mode"]    = "none"
        # returns no image
        self._current["image"] = "https://tvlistings.zap2it.com/assets/images/noImage165x220.jpg"

        # Sometimes tivo returns 'no_channel Video' from a status request.
        if words[0] == 'no_channel' or len(words) < 3:
            return

        if words[0] == "CH_STATUS":
            # subchannel?
            if len(words) == 4:
                channel = words[1].lstrip("0") + "." + words[2].lstrip("0")
                channel = channel.zfill(4)
                status = words[3]
            else:
                channel = words[1].lstrip("0")
                status = words[2]

            self._current["channel"] = channel
            self._current["title"]   = "Ch. {}".format(channel)
            self._current["status"]  = status
            self._current["mode"]    = "TV"

        if self.zapclient:
            zap_ch = channel.replace('-', '.')      # maybe not needed
            ch  = self.zapclient.get_callsign(zap_ch)
            self._current["channel"] = ch
            num = zap_ch.lstrip("0")
            ti  = self.zapclient.get_title(zap_ch)
            if self.debug:
                _LOGGER.info("Channel:  %s", num)
                _LOGGER.info("Callsign: %s", ch)
                _LOGGER.info("Title:    %s", ti)

            self._current["title"] = "Ch. {} {}: {}".format(num, ch, ti)
            self._current["image"] = self.zapclient.get_image_url(zap_ch)

        self._is_standby = False

    def send_code(self, code, cmdtype="IRCODE", extra=0, bufsize=1024):
        data = ""
        if extra:
            code = code + " " + extra
            # can be '', IRCODE, KEYBOARD, or TELEPORT.  Usually it's IRCODE but we might switch to KEYBOARD since it can do more.

        try:
            self.connect(self._host, self._port)
            if code:
                if cmdtype == '':
                    tosend = code + "\r"
                else:
                    tosend = cmdtype + " " + code + "\r"
            else:
                tosend = ""

            if self.debug:
                _LOGGER.debug("Sending request: '%s'", tosend)

            try:
                self.sock.sendall(tosend.encode())
                time.sleep(0.3)
                data = self.sock.recv(bufsize)
                if self.debug:
                    _LOGGER.debug("Received response: '%s'", data)
            except socket.timeout:
                if self.debug:
                    _LOGGER.warning("Connection timed out...")
                data = b'no_channel Video'

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
            return STATE_STANDBY
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

    @property
    def show_vod(self):
        data = b""
        """ Activate Video on demand menu """
        data = self.send_code('VIDEO_ON_DEMAND','KEYBOARD')
        self._current["mode"] = "VIDEO"
        return data.decode()

    def channel_set(self, channel):
        """Channel set."""
        data = self.show_live()
        #if(data.trim() == "LIVETV_READY"):
        self.send_code('SETCH', '', channel)

    def media_ch_up(self):
        """Channel up."""
        if self._current["mode"] == "TV":
            data = self.send_code('CHANNELUP')
            words = data.split()
            self.set_status(words)

    def media_ch_dn(self):
        """Channel down."""
        if self._current["mode"] == "TV":
            data = self.send_code('CHANNELDOWN')
            words = data.split()
            self.set_status(words)

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
    def media_image_url(self):
        """Return the image url of current playing media."""
        if self._is_standby:
            return None
        return self._current['image']

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

    def turn_on(self):
        """Turn on the receiver. """
        if self._is_standby:
            self.send_code('STANDBY','IRCODE')
            self._is_standby = False

    def turn_off(self):
        """Turn off the receiver. """
        if self._is_standby == False:
            self.send_code('STANDBY','IRCODE')
            self.send_code('STANDBY','IRCODE')
            self._is_standby = True

    def media_play(self):
        """Send play command."""
        if self._is_standby:
            return

        self.send_code('PLAY')

    def media_pause(self):
        """Send pause command."""
        if self._is_standby:
            return None

        self.send_code('PAUSE', 'IRCODE', 0, 0)

    def media_stop(self):
        """Send stop command. """
        if self._is_standby:
            return None

        if self._current["mode"] == "TV":
            return "INTV"

        data = self.send_code('STOP', 'IRCODE', 0, 0)
        words = data.split()
        return words[2]

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

        self.get_status()

    def media_next_track(self):
        """Send fast forward command."""
        if self._is_standby:
            return

        if self._current["mode"] in ("TV", "none"):
            self.media_ch_up()
        else:
            self.send_code('FORWARD', 'IRCODE', 0, 0)

        self.get_status()

class Zap2ItClient:

    def __init__(self, zapuser, zappass, debug=False):
        self._zapuser = zapuser
        self._zappass = zappass
        self.debug = debug

        self._channels = {}
        self._titles = {}
        self._images = {}

        self.update()
    
    def get_callsign(self, ch):
        return self._channels.get(ch)

    def get_title(self, ch):
        return self._titles.get(ch)

    def get_image_url(self, ch):
        return self._images.get(ch)

    def update(self):
        self.get_data()

    def login(self):
        # Login and fetch a token
        host = 'https://tvlistings.zap2it.com/'
        loginpath = 'api/user/login'
        favpath = 'api/user/favorites'
        login = host + loginpath

        tosend = {'emailid': self._zapuser, 'password': self._zappass, 'usertype': '0', 'facebookuser': 'false'}
        tosend_json = json.dumps(tosend).encode('utf8')
        header = {'content-type': 'application/json'}

        req = urllib.request.Request(url=login, data=tosend_json, headers=header, method='POST')
        res = urllib.request.urlopen(req, timeout=5)

        rawrtrn = res.read().decode('utf8')
        rtrn = json.loads(rawrtrn)

        self._token = rtrn['token']
        if self.debug:
             _LOGGER.debug("Zap token: %s", self._token)
        self._zapprops = rtrn['properties']

        self._zipcode = self._zapprops['2002']
        self._country = self._zapprops['2003']
        (self._lineupId, self._device) = self._zapprops['2004'].split(':')

    def get_data(self):
        #if self.debug:
        _LOGGER.debug("zapget_data called")
        self.login()
        now = int(time.time())
        self._channels = {}
        zap_params = self.get_zap_params()
        host = 'https://tvlistings.zap2it.com/'

        # Only get 1 hour of programming since we only need/want the current program titles
        #param = '?time=' + str(now) + '&timespan=0&pref=-&' + urlencode(zap_params) + '&TMSID=&FromPage=TV%20Grid&ActivityID=1&OVDID=&isOverride=true'
        param = '?time=' + str(now) + '&timespan=1&pref=-&' + urlencode(zap_params) + '&TMSID=&FromPage=TV%20Grid&ActivityID=1&OVDID=&isOverride=true'
        url = host + 'api/grid' + param
        if self.debug:
            _LOGGER.debug("Zapget url: %s", url)

        header = {'X-Requested-With': 'XMLHttpRequest'}

        req = urllib.request.Request(url=url,headers=header, method='GET')
        res = urllib.request.urlopen(req, timeout=5)

        #self._raw = res.read().decode('utf8')
        #self._zapraw = json.loads(self._raw)
        #self._zapraw = json.loads(res.read().decode('utf8'))

        if self.debug:
            self._raw = res.read().decode('utf8')
            self._zapraw = json.loads(self._raw)

            f = open('/tmp/zapraw','w')
            f.write(self._raw)
            f.close()
        else:
            self._zapraw = json.loads(res.read().decode('utf8'))

        self.get_channels()
        self.get_titles()

    def get_channels(self):
        # Decode basic channel num to channel name from zap raw data
        if self.debug:
            _LOGGER.info("zapget_channels called")
        for channelData in self._zapraw['channels']:
            # Pad channel numbers to 4 chars to match values from Tivo device
            _ch = channelData['channelNo'].zfill(4)
            self._channels[_ch] = channelData['callSign']

    def get_titles(self):
        # Decode program titles from zap raw data
        if self.debug:
            _LOGGER.info("zapget_titles called")
        self._titles = {}
        self._images = {}
        #self._start  = {}
        #self._end    = {}

        for channelData in self._zapraw['channels']:
            _ch = channelData['channelNo'].zfill(4)
            _ev = channelData['events']

            tmp = _ev[0]
            prog = tmp['program']

            start_utc  = time.strptime(tmp['startTime'], "%Y-%m-%dT%H:%M:%SZ")
            start_time = timegm(start_utc)
#            starthm    = time.strftime("%H:%M",  timezone('US/Central').localize(start_utc))

            end_utc    = time.strptime(tmp['endTime'], "%Y-%m-%dT%H:%M:%SZ")
            end_time   = timegm(end_utc)
#            #endhm   = time.strftime("%H:%M", end_utc)
#            endhm   = time.strftime("%H:%M", timezone('US/Central').localize(end_utc))
#
#            pgmtime = ' (' + starthm + ' - ' + endhm + ')'

            try:
                if tmp['thumbnail'] != "":
                    image = "https://zap2it.tmsimg.com/assets/" + tmp['thumbnail'] + ".jpg"
                else:
                    image = "https://tvlistings.zap2it.com/assets/images/noImage165x220.jpg"
            except:
                image = "https://tvlistings.zap2it.com/assets/images/noImage165x220.jpg"
            self._images[_ch] = image

            now = int(time.time())
            if start_time < now < end_time:
                title = prog['title']
                self._titles[_ch] = title
                try:
                    if tmp['thumbnail'] != "":
                        image = "https://zap2it.tmsimg.com/assets/" + tmp['thumbnail'] + ".jpg"
                    else:
                        image = "https://tvlistings.zap2it.com/assets/images/noImage165x220.jpg"
                except:
                    image = "https://tvlistings.zap2it.com/assets/images/noImage165x220.jpg"
                self._images[_ch] = image
                # + pgmtime

    def get_zap_params(self):
        zparams = {}

        self._postalcode = self._zipcode
        country = 'USA'
        device = 'X'

        if re.match('[A-z]', self._zipcode):
            country = 'CAN'

            print("testing zlineupid: %s\n" % zlineupId)
            if re.match(':', zlineupId):
                (lineupId, device) = zlineupId.split(':')
            else:
                lineupId = zlineupId
                device   = '-'

            zparams['postalCode'] = self._postalcode
        else:
            zparams['token'] = self._token

        zparams['lineupId']    = self._country + '-' + self._lineupId + '-DEFAULT'
        zparams['headendId']   = self._lineupId
        zparams['device']      = device
        zparams['postalCode']  = self._postalcode
        zparams['country']     = self._country
        zparams['aid']         = 'gapzap'

        return zparams
