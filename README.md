# homeassistant-tivo
Tivo component for Home Assistant

Based on ideas from the following sites:

```
https://community.home-assistant.io/t/control-tivo-box-over-telnet/12430/65
https://www.tivocommunity.com/community/index.php?threads/tivo-ui-control-via-telnet-no-hacking-required.392385/
https://community.home-assistant.io/t/tivo-media-player-component/851
https://charliemeyer.net/2012/12/04/remote-control-of-a-tivo-from-the-linux-command-line/
```

So far, this code has a framework that is not fully implemented.  It should load if copied to the component/media_player directory.  It requires the following configuration:

```
media_player:
  - platform: tivo
    host: 192.168.0.22
    name: Tivo
    port: 31339
    device: 0
```

This works by opening a socket connection to the Tivo device on its default port 31339.  Then using the following protocol, it can perform several commands:

https://www.tivo.com/assets/images/abouttivo/resources/downloads/brochures/TiVo_TCP_Network_Remote_Control_Protocol.pdf

Then it reads the response and should try to parse that information to determine status.  Simply connecting without sending a command, as we do in __init__, responds with status such as:

```
CH_STATUS 0613 LOCAL
```

This means channel status, channel 613, and channel was set by the remote.  If we set the channel, it should say REMOTE instead of LOCAL, or RECORDING if a recording is in process.

More to come...

