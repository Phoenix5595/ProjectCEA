/** Shared service-status presentation used by the operations rail and MothernodeRibbon. */
import type { SystemStats } from '../../hooks/useSystemStatus';

export type ServiceStatus = SystemStats['services'][number]['status'];

export interface ServiceStatusPresentation {
  label: string;
  dotClass: string;
  textClass: string;
}

const SERVICE_STATUS_PRESENTATION: Record<ServiceStatus, ServiceStatusPresentation> = {
  running: {
    label: 'running',
    dotClass: 'bg-status-success-vivid',
    textClass: 'text-status-success-text',
  },
  stopped: {
    label: 'stopped',
    dotClass: 'bg-status-warning-vivid',
    textClass: 'text-status-warning-text',
  },
  error: {
    label: 'error',
    dotClass: 'bg-status-danger-vivid',
    textClass: 'text-status-danger-text',
  },
  unreachable: {
    label: 'unreachable',
    dotClass: 'bg-status-danger-vivid',
    textClass: 'text-status-danger-text',
  },
};

export function getServiceStatusPresentation(status: ServiceStatus): ServiceStatusPresentation {
  return SERVICE_STATUS_PRESENTATION[status] ?? SERVICE_STATUS_PRESENTATION.error;
}
