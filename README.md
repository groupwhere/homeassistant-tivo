# homeassistant-tivo
Tivo component for Home Assistant

Based on ideas from the following sites:

https://community.home-assistant.io/t/control-tivo-box-over-telnet/12430/65
https://www.tivocommunity.com/community/index.php?threads/tivo-ui-control-via-telnet-no-hacking-required.392385/
https://community.home-assistant.io/t/tivo-media-player-component/851
https://charliemeyer.net/2012/12/04/remote-control-of-a-tivo-from-the-linux-command-line/

So far, this code has a framework that is not fully implemented.  It should load if copied to the component directory.  It requires the following configuration:

```
media_player:
  - platform: tivo
    host: 192.168.0.22
    name: Tivo
    port: 31339
    device: 0
```
