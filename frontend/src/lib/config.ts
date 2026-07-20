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
    const cfg = await configPromise
    return cfg.apiUrl
  }

  // Start fetching config
  configPromise = fetchConfig()
  const cfg = await configPromise
  return cfg.apiUrl
}

/**
 * Get the full configuration.
 */
export async function getConfig(): Promise<AppConfig> {
  if (config) {
    return config
  }

  if (configPromise) {
    return await configPromise
  }

  configPromise = fetchConfig()
  return await configPromise
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
  let apiUrlSource: 'runtime' | 'environment' | 'fallback' | 'override' = 'fallback'
  try {
    if (isDev) console.log('?? [Config] Attempting to fetch runtime config from /config endpoint...')
    const runtimeResponse = await fetch('/config', {
      cache: 'no-store',
    })
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

  // Priority: Runtime config > Build-time env var > Smart default
  // Note: runtimeApiUrl must be checked against null explicitly as empty string might be valid if intended (though we treat '' as null above)
  const baseUrl = overrideApiUrl || (runtimeApiUrl !== null && runtimeApiUrl !== undefined ? runtimeApiUrl : (envApiUrl || defaultApiUrl))
  if (overrideApiUrl) {
    apiUrlSource = 'override'
  } else if (runtimeApiUrl !== null && runtimeApiUrl !== undefined) {
    apiUrlSource = 'runtime'
  } else if (envApiUrl) {
    apiUrlSource = 'environment'
  } else {
    apiUrlSource = 'fallback'
  }
  if (isDev) {
    console.log('?? [Config] Final base URL to try:', baseUrl)
    console.log('?? [Config] Selection priority: runtime=' + (runtimeApiUrl ? '?' : '?') +
                ', build-time=' + (envApiUrl ? '?' : '?') +
                ', override=' + (overrideApiUrl ? '?' : '?') +
                ', smart-default=' + (!runtimeApiUrl && !envApiUrl && !overrideApiUrl ? '?' : '?'))
  }

  try {
    if (isDev) console.log('?? [Config] Fetching backend config from:', `${baseUrl}/api/config`)
    // Try to fetch runtime config from backend API
    const response = await fetch(`${baseUrl}/api/config`, {
      cache: 'no-store',
    })

    if (response.ok) {
      const data: BackendConfigResponse = await response.json()
      config = {
        apiUrl: baseUrl, // Use baseUrl from runtime-config (Python no longer returns this)
        apiUrlSource,
        deploymentMode,
        version: data.version || 'unknown',
        buildTime: BUILD_TIME,
        latestVersion: data.latestVersion || null,
        hasUpdate: data.hasUpdate || false,
        dbStatus: data.dbStatus, // Can be undefined for old backends
      }
      if (isDev) console.log('? [Config] Successfully loaded API config:', config)
      return config
    } else {
      // Don't log error here - ConnectionGuard will display it
      throw new Error(`API config endpoint returned status ${response.status}`)
    }
  } catch (error) {
    // Don't log error here - ConnectionGuard will display it with proper UI
    throw error
  }
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
