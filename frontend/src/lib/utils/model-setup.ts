import { Model } from '@/lib/types/models'

type ModelType = Model['type']

export const PROVIDER_MODALITIES: Record<string, ModelType[]> = {
  openai: ['language', 'embedding', 'text_to_speech', 'speech_to_text'],
  anthropic: ['language'],
  google: ['language', 'embedding'],
  groq: ['language', 'speech_to_text'],
  mistral: ['language', 'embedding', 'speech_to_text', 'text_to_speech'],
  deepseek: ['language'],
  xai: ['language', 'text_to_speech'],
  openrouter: ['language'],
  voyage: ['embedding'],
  elevenlabs: ['text_to_speech', 'speech_to_text'],
  deepgram: ['text_to_speech'],
  ollama: ['language', 'embedding'],
  azure: ['language', 'embedding', 'text_to_speech', 'speech_to_text'],
  vertex: ['language', 'embedding', 'text_to_speech'],
  openai_compatible: ['language', 'embedding', 'text_to_speech', 'speech_to_text'],
  dashscope: ['language'],
  minimax: ['language'],
}

export function isSupportedProviderModality(model: Model): boolean {
  const supported = PROVIDER_MODALITIES[model.provider]
  return !supported || supported.includes(model.type)
}

export function pickProviderSetupModels(models: Model[], provider: string) {
  const eligible = models
    .filter((model) => model.provider === provider && isSupportedProviderModality(model))
    .sort((left, right) => left.name.localeCompare(right.name))
  return {
    chatModel: eligible.find((model) => model.type === 'language') || null,
    embeddingModel: eligible.find((model) => model.type === 'embedding') || null,
  }
}

export function localOllamaBaseUrl(connection: 'same-machine' | 'docker'): string {
  return connection === 'docker'
    ? 'http://host.docker.internal:11434'
    : 'http://localhost:11434'
}

export function providerSetupReadiness(
  models: Model[],
  provider: string,
  options: { credentialConfigured: boolean; connectionPassed: boolean },
) {
  const selected = pickProviderSetupModels(models, provider)
  const blockers = [
    ...(!options.credentialConfigured ? ['credential_missing'] : []),
    ...(options.credentialConfigured && !options.connectionPassed ? ['connection_not_verified'] : []),
    ...(!selected.chatModel ? ['chat_model_missing'] : []),
    ...(!selected.embeddingModel ? ['embedding_model_missing'] : []),
  ]
  return { ...selected, ready: blockers.length === 0, blockers }
}
