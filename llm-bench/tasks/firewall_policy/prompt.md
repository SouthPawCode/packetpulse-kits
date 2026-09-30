You are writing the inter-VLAN firewall policy for a UniFi UDM Pro, from the plan below.

{{ file:firewall_policy/vlan_plan.md }}

Write the complete ordered rule list. Rules are evaluated top to bottom and the first match wins, so order matters.

Output exactly one fenced ```yaml block containing a YAML list. Each rule has these keys:
- name: short description (put "established, related" in the name of the return-traffic rule)
- action: allow or block
- source: a network name from the plan, a host IP, or "any"
- destination: a network name from the plan, a host IP, "internet" for the WAN, or "any"
- protocol: tcp, udp or any
- ports: a list of destination ports, or "any"

After the block, add at most three sentences explaining any assumption you made.
