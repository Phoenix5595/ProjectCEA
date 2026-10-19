import type { ReactNode } from 'react'

import type { GuideStep } from './nextcloudConnectionErrors'

interface NextcloudTailscaleGuideProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  highlightStep: GuideStep | null
}

function Command({ children }: { children: string }) {
  return (
    <pre className="mt-1 overflow-x-auto rounded bg-surface-default p-2 text-xs">
      <code>{children}</code>
    </pre>
  )
}

const STEPS: Array<{ id: GuideStep; title: string; body: ReactNode }> = [
  {
    id: 'tailscale',
    title: 'Put the Nextcloud machine on your tailnet.',
    body: (
      <>
        <p>Install Tailscale on the machine running Nextcloud and log in with the same account as this Pi.</p>
        <Command>{'curl -fsSL https://tailscale.com/install.sh | sh\nsudo tailscale up'}</Command>
        <p className="mt-1">In the Tailscale admin console (DNS page), keep MagicDNS on and enable HTTPS Certificates.</p>
      </>
    ),
  },
  {
    id: 'https',
    title: 'Publish Nextcloud over HTTPS with Tailscale Serve.',
    body: (
      <>
        <p>
          On the Nextcloud machine, where <code>{'<port>'}</code> is the local HTTP port Nextcloud listens on:
        </p>
        <Command>{'sudo tailscale serve --bg http://127.0.0.1:<port>\ntailscale serve status'}</Command>
        <p className="mt-1">
          Nextcloud is now at https://&lt;machine&gt;.&lt;tailnet&gt;.ts.net on port 443, with a valid certificate and
          reachable only from your tailnet. Use port 443; do not add a port to the URL.
        </p>
      </>
    ),
  },
  {
    id: 'nextcloud-domain',
    title: 'Tell Nextcloud about its new address.',
    body: (
      <>
        <Command>
          {'sudo -u www-data php occ config:system:set trusted_domains 10 --value=<machine>.<tailnet>.ts.net\nsudo -u www-data php occ config:system:set overwriteprotocol --value=https'}
        </Command>
        <p className="mt-1">
          If Nextcloud runs in Docker, prefix each command with <code>{'docker exec -u www-data <container>'}</code> and
          drop <code>sudo -u www-data</code>. Open https://&lt;machine&gt;.&lt;tailnet&gt;.ts.net in a browser on a
          tailnet device to confirm the login page loads.
        </p>
      </>
    ),
  },
  {
    id: 'calendar',
    title: 'Create the Growing calendar.',
    body: (
      <p>
        In Nextcloud, open Calendar and create a calendar named exactly Growing. The Pi writes grow events only to this
        calendar.
      </p>
    ),
  },
  {
    id: 'app-password',
    title: 'Create an app password.',
    body: (
      <p>
        In Nextcloud: your avatar → Personal settings → Security → Devices &amp; sessions. Enter a name such as
        ProjectCEA Pi and choose Create new app password. Copy the password; Nextcloud shows it once. The account also
        needs Nextcloud Files, which grow archives are uploaded to.
      </p>
    ),
  },
  {
    id: 'form',
    title: 'Fill in this form.',
    body: (
      <p>
        CalDAV base URL: https://&lt;machine&gt;.&lt;tailnet&gt;.ts.net/remote.php/dav/. Username: your Nextcloud login
        name. App password: the password from step 5. Press Test, choose Growing, press Save, then Reconnect and mirror
        all local events.
      </p>
    ),
  },
]

export function NextcloudTailscaleGuide({ open, onOpenChange, highlightStep }: NextcloudTailscaleGuideProps) {
  return (
    <details
      open={open}
      onToggle={event => {
        // Programmatic open changes also fire toggle; report only user-driven differences.
        if (event.currentTarget.open !== open) onOpenChange(event.currentTarget.open)
      }}
      className="mb-4 rounded border border-border-subtle bg-surface-secondary px-3 py-2 text-sm text-text-default"
    >
      <summary className="cursor-pointer font-medium">How to connect Nextcloud over Tailscale</summary>
      <ol className="mt-2 list-decimal space-y-3 pl-5">
        {STEPS.map(step => {
          const highlighted = step.id === highlightStep
          return (
            <li
              key={step.id}
              id={`nextcloud-guide-${step.id}`}
              className={highlighted ? 'rounded border border-accent-vivid p-2' : undefined}
              aria-current={highlighted ? 'step' : undefined}
            >
              <p className="font-medium">{step.title}</p>
              {step.body}
            </li>
          )
        })}
      </ol>
    </details>
  )
}
