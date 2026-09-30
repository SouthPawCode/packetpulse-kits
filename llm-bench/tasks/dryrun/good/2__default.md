1. Root cause: sw-agg-1 port 7, the SFP+ DAC to pve2, has a failing physical link (rx CRC errors climbing 312 -> 48,211 with repeated link down/up flaps). A bad DAC cable or transceiver is the most likely fault.

2. Symptoms: the repeated "port 7 link down/up" alerts, the STP topology change, corosync link 0 down and token timeouts on pve2, Ceph osd heartbeat failures, osds marked down and degraded objects, slow ops, pve-ha-lrm restarting vm:104 without quorum, the failed PBS backup to pve2, the nextcloud guest agent and client unreachable alerts, and the nas01 reconnect.

3. Unrelated noise: udm-pro CPU spike during its scheduled backup, the SMART warning on the unused spare /dev/sdc on pve3, the firmware-available notice, the brief WAN latency blip at 01:55, the DFS radar channel change, guest WLAN retries, the iPhone roam, and the pve1 certificate expiry notice.

4. First step: swap the DAC cable (or move pve2 to another SFP+ port) on sw-agg-1 port 7, then clear counters and watch rx_crc_errors and corosync for 15 minutes before touching Ceph or the VM.
