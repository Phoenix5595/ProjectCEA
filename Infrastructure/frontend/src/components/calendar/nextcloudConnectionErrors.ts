export type GuideStep = 'tailscale' | 'https' | 'nextcloud-domain' | 'calendar' | 'app-password' | 'form'

export interface CalendarErrorHelp {
  message: string
  step: GuideStep | null
}

const NEXTCLOUD_DAV_URL = 'https://<machine>.<tailnet>.ts.net/remote.php/dav/'

const ERROR_HELP: Record<string, CalendarErrorHelp> = {
  calendar_url_invalid: {
    message: `The CalDAV base URL must start with https:// and have no user name, query or fragment. Use ${NEXTCLOUD_DAV_URL}`,
    step: 'form',
  },
  calendar_auth_failed: {
    message: 'Nextcloud rejected the user name or app password. Create a new app password and use your Nextcloud login name.',
    step: 'app-password',
  },
  calendar_transport_failed: {
    message: 'The Pi could not reach Nextcloud over HTTPS. Check that both machines are on the tailnet, Tailscale Serve is running, and HTTPS certificates are enabled.',
    step: 'https',
  },
  calendar_collection_unavailable: {
    message: 'Nextcloud answered but the calendar was not found. Check the base URL ends in /remote.php/dav/ and a calendar named "Growing" exists.',
    step: 'calendar',
  },
  calendar_files_unavailable: {
    message: 'Calendar works but Nextcloud Files is not writable for this account. Use an account with Files enabled and full permissions on its home folder.',
    step: 'app-password',
  },
  calendar_remote_conflict: {
    message: `Nextcloud redirected or returned an unexpected answer. Use the exact URL ${NEXTCLOUD_DAV_URL} (no redirects) and check Nextcloud's trusted domain and https overwrite settings.`,
    step: 'nextcloud-domain',
  },
  calendar_remote_response_invalid: {
    message: 'Nextcloud returned a response that is not a valid DAV answer. Check the base URL points at /remote.php/dav/ and not at the web interface.',
    step: 'form',
  },
  calendar_instance_changed: {
    message: 'This URL points at a different Nextcloud than the saved connection. Remove the connection or use the original server.',
    step: 'form',
  },
  calendar_credentials_unavailable: {
    message: 'The Pi cannot store credentials right now (encryption key unavailable). Check the automation service configuration.',
    step: null,
  },
  calendar_connection_test_failed: {
    message: 'The connection test failed unexpectedly. Check every guide step, then try again.',
    step: null,
  },
}

const GENERIC_HELP: CalendarErrorHelp = {
  message: 'Connection failed. Follow the guide below and try again.',
  step: null,
}

export function calendarErrorCode(error: unknown): string | null {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = error.response
    const data = typeof response === 'object' && response !== null && 'data' in response ? response.data : undefined
    const detail = typeof data === 'object' && data !== null && 'detail' in data ? data.detail : undefined
    if (typeof detail === 'string') return detail
    if (typeof detail === 'object' && detail !== null && 'code' in detail) {
      const code = detail.code
      if (typeof code === 'string') return code
    }
  }
  return null
}

export function describeCalendarError(code: string | null): CalendarErrorHelp {
  if (code !== null && Object.hasOwn(ERROR_HELP, code)) return ERROR_HELP[code]
  return GENERIC_HELP
}
