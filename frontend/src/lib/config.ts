/**
 * Runtime configuration for the frontend.
 * This allows the same Docker image to work in different environments.
 */

import { AppConfig, BackendConfigResponse } from '@/lib/types/config'

// Build timestamp for debugging - set at build time
const BUILD_TIME = new Date().toISOString()
const DEPLOYMENT_OVERRIDE_KEY = 'deployment-mode-override'
const API_URL_OVERRIDE_KEY = 'api-url-override'

let config: AppConfig | null = null
let configPromise: Promise<AppConfig> | null = null
const CONFIG_REQUEST_TIMEOUT_MS = 1500

async function fetchConfigEndpoint(url: string): Promise<Response> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), CONFIG_REQUEST_TIMEOUT_MS)
  try {
    return await fetch(url, {
      cache: 'no-store',
      signal: controller.signal,
    })
  } finally {
    clearTimeout(timeout)
  }
}

function getDeploymentOverride(): 'auto' | 'local' | 'custom' | null {
  if (typeof window === 'undefined') return null
  try {
    const value = localStorage.getItem(DEPLOYMENT_OVERRIDE_KEY)
    if (value === 'auto' || value === 'local' || value === 'custom') return value
  } catch {
    // ignore storage access failures
  }
  return null
}

function resolveOverrideApiUrl(mode: 'local' | 'custom' | null): string | null {
  if (mode === 'local') return 'http://localhost:5055'
  if (mode === 'custom') {
    if (typeof window === 'undefined') return null
    try {
      const customUrl = localStorage.getItem(API_URL_OVERRIDE_KEY)
      return customUrl && customUrl.trim() ? customUrl.trim() : null
    } catch {
      return null
    }
  }
  return null
}

/**
 * Get the API URL to use for requests.
 *
 * Priority:
 * 1. Runtime config from API server (/api/config endpoint)
 * 2. Environment variable (NEXT_PUBLIC_API_URL)
 * 3. Default fallback (http://localhost:5055)
 */
export async function getApiUrl(): Promise<string> {
  // If we already have config, return it
  if (config) {
    return config.apiUrl
  }

  // If we're already fetching, wait for that
  if (configPromise) {
    try {
      const cfg = await configPromise
      return cfg.apiUrl
    } catch (error) {
      configPromise = null
      throw error
    }
  }

  // Start fetching config
  configPromise = fetchConfig()
  try {
    const cfg = await configPromise
    return cfg.apiUrl
  } catch (error) {
    configPromise = null
    throw error
  }
}

/**
 * Get the full configuration.
 */
export async function getConfig(): Promise<AppConfig> {
  if (config) {
    return config
  }

  if (configPromise) {
    try {
      return await configPromise
    } catch (error) {
      configPromise = null
      throw error
    }
  }

  configPromise = fetchConfig()
  try {
    return await configPromise
  } catch (error) {
    configPromise = null
    throw error
  }
}

/**
 * Fetch configuration from the API or use defaults.
 */
async function fetchConfig(): Promise<AppConfig> {
  const isDev = process.env.NODE_ENV === 'development'
  const deploymentMode = getDeploymentOverride() || 'auto'
  const overrideApiUrl = resolveOverrideApiUrl(deploymentMode === 'local' ? 'local' : deploymentMode === 'custom' ? 'custom' : null)

  if (isDev) {
    console.log('?? [Config] Starting configuration detection...')
    console.log('?? [Config] Build time:', BUILD_TIME)
  }

  // STEP 1: Try to get runtime config from Next.js server-side endpoint
  // This allows API_URL to be set at runtime (not baked into build)
  // Note: Endpoint is at /config (not /api/config) to avoid reverse proxy conflicts
  let runtimeApiUrl: string | null = null
  try {
    if (isDev) console.log('?? [Config] Attempting to fetch runtime config from /config endpoint...')
    const runtimeResponse = await fetchConfigEndpoint('/config')
    if (runtimeResponse.ok) {
      const runtimeData = await runtimeResponse.json()
      runtimeApiUrl = typeof runtimeData.apiUrl === 'string' && runtimeData.apiUrl.trim()
        ? runtimeData.apiUrl.trim()
        : null
      if (isDev) console.log('[Config] Runtime API URL from server:', runtimeApiUrl || '(not set)')
    } else {
      if (isDev) console.log('?? [Config] Runtime config endpoint returned status:', runtimeResponse.status)
    }
  } catch (error) {
    if (isDev) console.log('?? [Config] Could not fetch runtime config:', error)
  }

  // STEP 2: Fallback to build-time environment variable
  const envApiUrl = process.env.NEXT_PUBLIC_API_URL
  if (isDev) console.log('?? [Config] NEXT_PUBLIC_API_URL from build:', envApiUrl || '(not set)')

  // STEP 3: Smart default - prefer relative path to use Next.js Rewrites
  // This avoids CORS issues and port mapping complexities by proxying through Next.js
  const defaultApiUrl = ''

  if (typeof window !== 'undefined' && isDev) {
      console.log('?? [Config] Using relative path (rewrites) as default')
  }

  // Prefer the same-origin Next.js proxy over a build-time URL. This keeps a
  // stale development port baked into an old bundle from delaying every first
  // request, and it also works through ngrok/Docker without exposing an API
  // port. Explicit browser/runtime overrides still retain their priority.
  type ApiUrlSource = 'runtime' | 'environment' | 'fallback' | 'override'
  const candidates: Array<{ url: string; source: ApiUrlSource }> = []
  const addCandidate = (url: string | null | undefined, source: ApiUrlSource) => {
    if (url === null || url === undefined) return
    if (candidates.some((candidate) => candidate.url === url)) return
    candidates.push({ url, source })
  }
  addCandidate(overrideApiUrl, 'override')
  addCandidate(runtimeApiUrl, 'runtime')
  addCandidate(defaultApiUrl, 'fallback')
  addCandidate(envApiUrl, 'environment')

  if (candidates.length === 0) {
    throw new Error('No API URL candidate is configured')
  }
  if (isDev) {
    console.log('?? [Config] API URL candidates:', candidates.map((candidate) => candidate.url))
    console.log('?? [Config] Selection priority: runtime=' + (runtimeApiUrl ? '?' : '?') +
                ', build-time=' + (envApiUrl ? '?' : '?') +
                ', override=' + (overrideApiUrl ? '?' : '?') +
                ', smart-default=' + (!runtimeApiUrl && !envApiUrl && !overrideApiUrl ? '?' : '?'))
  }

  let lastError: unknown = null
  for (const candidate of candidates) {
    try {
      if (isDev) console.log('?? [Config] Fetching backend config from:', `${candidate.url}/api/config`)
      const response = await fetchConfigEndpoint(`${candidate.url}/api/config`)

      if (!response.ok) {
        lastError = new Error(`API config endpoint returned status ${response.status}`)
        continue
      }

      const data: BackendConfigResponse = await response.json()
      config = {
        apiUrl: candidate.url, // Use the first reachable candidate.
        apiUrlSource: candidate.source,
        deploymentMode,
        version: data.version || 'unknown',
        buildTime: BUILD_TIME,
          latestVersion: data.latestVersion || null,
          hasUpdate: data.hasUpdate || false,
          dbStatus: data.dbStatus, // Can be undefined for old backends
          systemName: data.systemName || 'Pháp luật Hải Phòng',
          organizationName: data.organizationName || '',
        }
      if (isDev) console.log('? [Config] Successfully loaded API config:', config)
      return config
    } catch (error) {
      lastError = error
      if (candidate.source === 'override' && typeof window !== 'undefined') {
        // An explicit endpoint that can no longer be reached must not penalize
        // every page load. Return to auto detection; the user can still set a
        // new custom endpoint from Settings when it becomes available.
        try {
          localStorage.removeItem(DEPLOYMENT_OVERRIDE_KEY)
          localStorage.removeItem(API_URL_OVERRIDE_KEY)
        } catch {
          // Ignore storage failures and continue with the reachable candidates.
        }
      }
      if (isDev) console.warn('[Config] API candidate failed, trying next candidate:', candidate.url)
    }
  }

  // Runtime metadata is useful, but a transient timeout must not blank the
  // entire application. The same-origin proxy remains the safest transport;
  // concrete API calls still report a localized connection error if the
  // backend itself is unavailable.
  const sameOriginFallback = candidates.find((candidate) => candidate.source === 'fallback')
  if (sameOriginFallback) {
    config = {
      apiUrl: sameOriginFallback.url,
      apiUrlSource: 'fallback',
      deploymentMode,
      version: 'unknown',
      buildTime: BUILD_TIME,
      latestVersion: null,
      hasUpdate: false,
      systemName: 'Pháp luật Hải Phòng',
      organizationName: '',
    }
    return config
  }

  // Don't log error here - ConnectionGuard will display it with proper UI.
  throw lastError instanceof Error ? lastError : new Error('Unable to connect to API')
}

/**
 * Reset the configuration cache (useful for testing).
 */
export function resetConfig(): void {
  config = null
  configPromise = null
}

export function setDeploymentModeOverride(mode: 'auto' | 'local' | 'custom', customUrl?: string): void {
  if (typeof window === 'undefined') return
  try {
    if (mode === 'auto') {
      localStorage.removeItem(DEPLOYMENT_OVERRIDE_KEY)
      localStorage.removeItem(API_URL_OVERRIDE_KEY)
    } else {
      localStorage.setItem(DEPLOYMENT_OVERRIDE_KEY, mode)
      if (mode === 'custom' && customUrl) {
        localStorage.setItem(API_URL_OVERRIDE_KEY, customUrl)
      }
    }
  } catch {
    // ignore storage access failures
  }
  resetConfig()
}

export function getDeploymentModeOverride(): 'auto' | 'local' | 'custom' {
  return getDeploymentOverride() || 'auto'
}
