import apiClient from './client'

export type BackupComponent = {
  name: string
  source?: string
  required?: boolean
  status?: string
  file_count?: number
  bytes?: number
  warnings?: string[]
  reason?: string
}
export type BackupManifest = {
  backup_id: string
  created_at?: string | null
  completed_at?: string | null
  actor_id?: string | null
  reason?: string | null
  kind?: string | null
  status?: string | null
  warnings?: string[]
  components?: BackupComponent[]
  verification?: { passed?: boolean; checked_files?: number; mismatches?: string[]; checked_at?: string }
  restore_drill?: { passed?: boolean; status?: string; completed_at?: string }
  encryption?: { status?: string; format?: string | null; required?: boolean }
}

export type BackupPreflight = {
  backup_version?: string
  target_label?: string
  target_configured?: boolean
  writable: boolean
  free_bytes?: number | null
  estimated_file_bytes?: number
  reserved_bytes?: number
  write_jobs?: number
  components?: BackupComponent[]
  warnings?: string[]
  can_start: boolean
}

export const adminBackupsApi = {
  preflight: async () => (await apiClient.get<BackupPreflight>('/admin/backups/preflight')).data,
  list: async () => (await apiClient.get<{ items: BackupManifest[]; count: number }>('/admin/backups')).data,
  create: async (reason: string, kind: 'full' | 'application' | 'retrieval' = 'full') => (
    await apiClient.post<{ backup_id: string; status: string; message: string }>('/admin/backups', { reason, kind })
  ).data,
  get: async (backupId: string) => (await apiClient.get<BackupManifest>(`/admin/backups/${encodeURIComponent(backupId)}`)).data,
  verify: async (backupId: string) => (await apiClient.post<{ backup_id: string; passed: boolean; status: string; checked_files?: number; mismatches?: string[] }>(`/admin/backups/${encodeURIComponent(backupId)}/verify`)).data,
  restoreDrill: async (backupId: string) => (await apiClient.post<{ backup_id: string; passed: boolean; status: string; file_count?: number }>(`/admin/backups/${encodeURIComponent(backupId)}/restore-drill`)).data,
}
