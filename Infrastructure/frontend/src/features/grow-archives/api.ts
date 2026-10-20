/**
 * Typed archive-service client using the existing API-key-bearing client.
 *
 * GETs never begin a download or load. Only `load()` starts an explicit
 * selection; normal Nextcloud file navigation is a direct authenticated link,
 * never a JavaScript ZIP/Blob transfer.
 */
import type { AxiosInstance } from 'axios'

import { apiClient } from '../../services/api'

import {
  ArchiveDetail,
  ArchiveEntryPage,
  ArchiveExportResponse,
  ArchiveGraph,
  ArchiveJob,
  ArchiveLoadResponse,
} from './contracts'
import type { ArchiveRoom } from './contracts'

export const ARCHIVE_LOG_PAGE_SIZE = 10
export const ARCHIVE_GRAPH_MAX_POINTS = 1000

export interface ArchiveGraphRange {
  readonly start: Date
  readonly end: Date
}

export class ArchiveApi {
  constructor(private readonly client: AxiosInstance = apiClient.archiveClient) {}

  async list(
    room: ArchiveRoom,
    cursor?: string | null,
    signal?: AbortSignal
  ): Promise<ArchiveEntryPage> {
    const response = await this.client.get('/api/archives', {
      params: { room, cursor: cursor ?? undefined, limit: ARCHIVE_LOG_PAGE_SIZE },
      signal,
    })
    return ArchiveEntryPage.parse(response.data)
  }

  async detail(datasetId: string, signal?: AbortSignal): Promise<ArchiveDetail> {
    const response = await this.client.get(`/api/archives/${encodeURIComponent(datasetId)}`, {
      signal,
    })
    return ArchiveDetail.parse(response.data)
  }

  async exportDataset(
    datasetId: string,
    expectedRevision: number,
    signal?: AbortSignal
  ) {
    const response = await this.client.post(
      `/api/archives/${encodeURIComponent(datasetId)}/exports`,
      { expected_revision: expectedRevision },
      { signal }
    )
    return ArchiveExportResponse.parse(response.data)
  }

  /** Explicit Historical-log action only; no page/URL effect calls this. */
  async load(
    datasetId: string,
    expectedRevision: number,
    signal?: AbortSignal
  ) {
    const response = await this.client.post(
      `/api/archives/${encodeURIComponent(datasetId)}/loads`,
      { expected_revision: expectedRevision },
      { signal }
    )
    return ArchiveLoadResponse.parse(response.data)
  }

  async releaseLoad(loadId: string, signal?: AbortSignal): Promise<void> {
    await this.client.delete(`/api/archives/loads/${encodeURIComponent(loadId)}`, { signal })
  }

  async job(jobId: string, signal?: AbortSignal): Promise<ArchiveJob> {
    const response = await this.client.get(`/api/archives/jobs/${encodeURIComponent(jobId)}`, {
      signal,
    })
    return ArchiveJob.parse(response.data)
  }

  async retryJob(jobId: string, signal?: AbortSignal): Promise<void> {
    await this.client.post(`/api/archives/jobs/${encodeURIComponent(jobId)}/retry`, undefined, {
      signal,
    })
  }

  async graph(
    datasetId: string,
    loadId: string,
    range: ArchiveGraphRange,
    signal?: AbortSignal
  ): Promise<ArchiveGraph> {
    const start = range.start.getTime()
    const end = range.end.getTime()
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      throw new Error('archive_range_invalid')
    }
    const response = await this.client.get(
      `/api/archives/${encodeURIComponent(datasetId)}/graph`,
      {
        params: {
          load_id: loadId,
          start: range.start.toISOString(),
          end: range.end.toISOString(),
          max_points: ARCHIVE_GRAPH_MAX_POINTS,
        },
        signal,
      }
    )
    const graph = ArchiveGraph.parse(response.data)
    if (graph.archive.dataset_id !== datasetId) throw new Error('archive_integrity_failed')
    const responseIsOverview = 'bucket' in graph.sensors.metadata
    if ((graph.tier === 'overview') !== responseIsOverview) throw new Error('archive_integrity_failed')
    const responseRange = graph.sensors.metadata.range
    if (
      responseRange.start.getTime() !== start ||
      responseRange.end.getTime() !== end
    ) {
      throw new Error('archive_integrity_failed')
    }
    if (graph.tier === 'overview') {
      for (const sensor of graph.sensors.series) {
        for (const point of sensor.points) {
          if (point.start.getTime() < start || point.end.getTime() > end || point.end.getTime() <= point.start.getTime()) {
            throw new Error('archive_integrity_failed')
          }
        }
      }
    } else {
      for (const sensor of graph.sensors.series) {
        for (const point of sensor.points) {
          const timestamp = point.timestamp.getTime()
          if (timestamp < start || timestamp >= end) throw new Error('archive_integrity_failed')
        }
      }
    }
    for (const light of graph.lights) {
      for (const point of light.points) {
        if (point.start.getTime() < start || point.end.getTime() > end || point.end.getTime() <= point.start.getTime()) {
          throw new Error('archive_integrity_failed')
        }
      }
    }
    if ([...graph.photoperiod, ...graph.photoperiod_coverage].some(fact =>
      fact.time.getTime() < start || fact.time.getTime() >= end
    )) {
      throw new Error('archive_integrity_failed')
    }
    return graph
  }
}

/** Reject credential-bearing, tokenized, or non-TLS remote file URLs. */
export function isSafeNextcloudFileUrl(value: string | null): value is string {
  if (value === null) return false
  try {
    const url = new URL(value)
    if (url.username !== '' || url.password !== '' || url.search !== '' || url.hash !== '') {
      return false
    }
    if (url.protocol === 'https:') return true
    return (
      url.protocol === 'http:' &&
      (url.hostname === '127.0.0.1' || url.hostname === 'localhost' || url.hostname === '[::1]')
    )
  } catch {
    return false
  }
}
