# Automation Service Requirements

- Service runs under systemd (`automation-service.service`); restart after config/frontend builds: `sudo systemctl restart automation-service.service`.
- Climate modes supported: DAY, NIGHT, PRE_DAY, PRE_NIGHT. `ramp_in_duration` validated 0–240 minutes; PRE_DAY/PRE_NIGHT take precedence during their periods.
- Time parsing accepts `HH:MM` or `HH:MM:SS` strings.
- Database tables in use: `schedules`, `setpoints`, `pid_parameters`, `config_versions`, `effective_setpoints`. No unused tables identified for removal during latest audit.
- Keep UI/DB schema aligned for setpoints (modes + `ramp_in_duration`) and schedules (pre_day_duration, pre_night_duration).

