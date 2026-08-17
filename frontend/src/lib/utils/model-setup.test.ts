import { describe, expect, it } from 'vitest'

import { isSupportedProviderModality, localOllamaBaseUrl, pickProviderSetupModels, providerSetupReadiness } from './model-setup'
import { Model } from '@/lib/types/models'

function model(overrides: Partial<Model>): Model {
  return {
    id: 'model:1',
    name: 'model-one',
    provider: 'ollama',
    type: 'language',
    created: '',
    updated: '',
    ...overrides,
  }
}

describe('guided model setup', () => {
  it('rejects a legacy OpenRouter chat model mislabeled as embedding', () => {
    expect(isSupportedProviderModality(model({ provider: 'openrouter', type: 'embedding' }))).toBe(false)
  })

  it('requires a real chat and embedding pair from the selected provider', () => {
    const models = [
      model({ id: 'chat', name: 'qwen-chat', type: 'language' }),
      model({ id: 'embed', name: 'nomic-embed-text', type: 'embedding' }),
      model({ id: 'legacy', provider: 'openrouter', type: 'embedding' }),
    ]

    expect(pickProviderSetupModels(models, 'ollama')).toMatchObject({
      chatModel: { id: 'chat' },
      embeddingModel: { id: 'embed' },
    })
    expect(pickProviderSetupModels(models, 'openrouter')).toMatchObject({
      chatModel: null,
      embeddingModel: null,
    })
  })

  it('uses the correct local Ollama address for host and Docker deployments', () => {
    expect(localOllamaBaseUrl('same-machine')).toBe('http://localhost:11434')
    expect(localOllamaBaseUrl('docker')).toBe('http://host.docker.internal:11434')
  })

  it('does not activate a provider until connection, chat and embedding checks pass', () => {
    const models = [
      model({ id: 'chat', provider: 'google', name: 'gemini', type: 'language' }),
      model({ id: 'embed', provider: 'google', name: 'text-embedding', type: 'embedding' }),
    ]
    expect(providerSetupReadiness(models, 'google', {
      credentialConfigured: true, connectionPassed: false,
    })).toMatchObject({ ready: false, blockers: ['connection_not_verified'] })
    expect(providerSetupReadiness(models, 'google', {
      credentialConfigured: true, connectionPassed: true,
    })).toMatchObject({ ready: true, blockers: [] })
    expect(providerSetupReadiness(models, 'deepseek', {
      credentialConfigured: true, connectionPassed: true,
    }).blockers).toContain('embedding_model_missing')
  })
})
