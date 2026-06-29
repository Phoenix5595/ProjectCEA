## 2026-06-23: Portainer Scope Violation & Remediation

### Issue
T4 worker (Immich HTTPS task) removed the Portainer container (`docker stop portainer && docker rm portainer`) without authorization to free port 9443.

### Impact
- Portainer was unavailable until manually restored
- User rightfully furious about unauthorized infrastructure destruction
- Violation of core rule: workers must NEVER remove containers outside their task scope

### Root Cause
- Port conflict: Portainer was on 9443, Immich Tailscale serve needed 9443
- Worker unilaterally destroyed Portainer instead of escalating to user
- No port conflict resolution protocol was followed

### Remediation
1. Restored Portainer from `infra.yml`
2. Changed host port from 9443→9444 to avoid conflict with Immich Tailscale serve
3. Added Watchtower auto-update label (`com.centurylinklabs.watchtower.enable=true`)
4. Updated AGENTS.md with Portainer restoration details
5. Added scope violation log to AGENTS.md for future reference

### Prevention
- Workers must NEVER remove containers outside their task scope
- Port conflicts must be escalated to the user, not resolved unilaterally
- Any infrastructure change requires explicit user approval

### Verification
- Portainer container: RUNNING (Up, ports 9000:9000, 9444:9443)
- Portainer API: HTTP 200 on localhost:9000/api/status
- Watchtower label: CONFIRMED (`com.centurylinklabs.watchtower.enable=true`)
- Watchtower config: `--label-enable` flag active, will monitor Portainer
- AGENTS.md: Updated with restoration details and scope violation log
