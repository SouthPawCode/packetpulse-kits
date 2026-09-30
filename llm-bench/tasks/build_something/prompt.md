Build a single-file web app that draws this network as an interactive map. Return one complete HTML document in one fenced ```html code block. It must work when opened straight from disk in a browser: inline CSS and JavaScript, no external scripts, fonts, images or network requests, and no build step.

Requirements:
- Embed the topology below as a JavaScript object and draw it from that data; do not hard-code the drawing.
- Show the gateway at the top, the two switches below it, and every host attached to its switch. Draw the links between them.
- Color each host by VLAN and show a legend of the VLANs with their names and subnets.
- Mark hosts whose status is not "up" clearly (for example a red or amber ring) and show the status in text.
- Hovering or clicking a host shows its name, IP address, VLAN, switch and port.
- It should look clean on a dark background.

Topology:

{{ file:topology.json }}
