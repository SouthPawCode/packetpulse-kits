```yaml
- name: allow established, related return traffic
  action: allow
  source: any
  destination: any
  protocol: any
  ports: any
- name: IoT to Home Assistant
  action: allow
  source: IoT
  destination: 192.168.40.30
  protocol: tcp
  ports: [8123]
- name: Cameras to NVR
  action: allow
  source: Cameras
  destination: 192.168.40.20
  protocol: tcp
  ports: [7442]
- name: Trusted to Servers ssh and https
  action: allow
  source: Trusted
  destination: Servers
  protocol: tcp
  ports: [22, 443]
- name: Trusted to Management https
  action: allow
  source: Trusted
  destination: Management
  protocol: tcp
  ports: [443]
- name: block Cameras to internet
  action: block
  source: Cameras
  destination: internet
  protocol: any
  ports: any
- name: block Cameras to internal
  action: block
  source: Cameras
  destination: [Trusted, IoT, Guest, Servers, Management]
  protocol: any
  ports: any
- name: block Guest to internal
  action: block
  source: Guest
  destination: [Trusted, IoT, Servers, Cameras, Management]
  protocol: any
  ports: any
- name: block IoT to internal
  action: block
  source: IoT
  destination: [Trusted, Servers, Cameras, Management]
  protocol: any
  ports: any
- name: block Servers to Trusted and IoT
  action: block
  source: Servers
  destination: [Trusted, IoT]
  protocol: any
  ports: any
- name: allow Guest to internet
  action: allow
  source: Guest
  destination: internet
  protocol: any
  ports: any
- name: allow IoT to internet
  action: allow
  source: IoT
  destination: internet
  protocol: any
  ports: any
- name: allow Servers to internet
  action: allow
  source: Servers
  destination: internet
  protocol: tcp
  ports: [80, 443]
- name: default block between VLANs
  action: block
  source: any
  destination: any
  protocol: any
  ports: any
```
Assumption: Trusted-initiated traffic to IoT is not in the plan, so the final default rule blocks it.
