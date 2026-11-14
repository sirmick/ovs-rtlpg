#!/bin/sh
# alive.sh IP - is the switch's management CPU answering? prints UP or DOWN.
# HTTP (web server, main loop) + ARP (uIP, main loop): both stop when the loop hangs.
ip=$1
h=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://$ip/" 2>/dev/null)
if sudo arping -c 2 -w 3 -I eth0 "$ip" >/dev/null 2>&1; then a=yes; else a=no; fi
if [ "$h" != 000 ] && [ -n "$h" ] && [ "$a" = yes ]; then
  echo "UP (http=$h, arp=reply)"
else
  echo "DOWN (http=${h:-timeout}, arp=$a)"
fi
