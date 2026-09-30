Below is a syslog excerpt from a three-node Proxmox cluster ({{lines}} lines). Exactly one line is at severity ERROR for the service backup-agent. Other backup-agent lines carry similar-looking incident references at lower severity; those are not the answer.

Find that one ERROR line and reply with the incident ID it contains in this exact format, and nothing else:

INCIDENT: <id>

--- BEGIN LOG ---
{{log}}
--- END LOG ---

Reply with the incident ID from the backup-agent ERROR line, in the format INCIDENT: <id>.
