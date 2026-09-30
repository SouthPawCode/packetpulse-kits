NETWORKS (UDM Pro is the gateway; each VLAN's gateway address is .1)

| Name | VLAN | Subnet | Notes |
|---|---|---|---|
| Trusted | 10 | 192.168.10.0/24 | laptops, phones, desktops |
| IoT | 20 | 192.168.20.0/24 | smart plugs, printers, TVs |
| Guest | 30 | 192.168.30.0/24 | visitors |
| Servers | 40 | 192.168.40.0/24 | Proxmox, NAS. Home Assistant is 192.168.40.30, the NVR is 192.168.40.20 |
| Cameras | 50 | 192.168.50.0/24 | IP cameras |
| Management | 99 | 192.168.99.0/24 | switches, APs, the UDM Pro web UI |

REQUIREMENTS

1. Return traffic for connections that were already allowed must always work (established and related).
2. Guest devices get the internet only. They must not reach any internal network (Trusted, IoT, Servers, Cameras, Management).
3. IoT devices get the internet. They must not initiate connections to Trusted. The one exception to IoT's isolation is Home Assistant (192.168.40.30) on TCP 8123, which IoT devices must be able to reach. Every other IoT-to-Servers connection is blocked.
4. Cameras have no internet access at all. They must not reach any other internal network; the only thing they may initiate to is the NVR (192.168.40.20) on TCP 7442.
5. Trusted devices may reach Servers on TCP 22 and TCP 443 only, and may reach the Management network on TCP 443 only.
6. Servers may reach the internet for updates but must not initiate connections to Trusted or IoT.
7. Anything between VLANs that is not explicitly allowed is blocked.
