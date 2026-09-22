'use client'

import { useMemo, useState, useEffect, useId } from 'react'
import { useForm } from 'react-hook-form'
import { AppShell } from '@/components/layout/AppShell'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Key,
  ShieldAlert,
  AlertTriangle,
  Plus,
  Edit,
  Trash2,
  Plug,
  Loader2,
  Check,
  X,
  AlertCircle,
  Wand2,
  MessageSquare,
  Code,
  Mic,
  Volume2,
  Bot,
  Cloud,
  Laptop,
} from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useModels, useDeleteModel, useModelDefaults, useUpdateModelDefaults, useAutoAssignDefaults, useTestModel } from '@/lib/hooks/use-models'
import {
  useCredentials,
  useCredential,
  useCredentialStatus,
  useEnvStatus,
  useCreateCredential,
  useUpdateCredential,
  useDeleteCredential,
  useTestCredential,
  useDiscoverModels,
  useRegisterModels,
} from '@/lib/hooks/use-credentials'
import { Credential, CreateCredentialRequest, UpdateCredentialRequest, DiscoveredModel } from '@/lib/api/credentials'
import { Model, ModelDefaults } from '@/lib/types/models'
import { PROVIDER_MODALITIES, isSupportedProviderModality, localOllamaBaseUrl, providerSetupReadiness } from '@/lib/utils/model-setup'
import { formatApiError } from '@/lib/utils/error-handler'
import { MigrationBanner, ModelTestResultDialog } from '@/components/settings'
import { EmbeddingModelChangeDialog } from '@/components/settings/EmbeddingModelChangeDialog'
import { ChatModelPolicyForm } from '../components/ChatModelPolicyForm'

type ModelType = 'language' | 'embedding' | 'text_to_speech' | 'speech_to_text'

// Provider display names
const PROVIDER_DISPLAY_NAMES: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  google: 'Google AI',
  groq: 'Groq',
  mistral: 'Mistral AI',
  deepseek: 'DeepSeek',
  xai: 'xAI (Grok)',
  openrouter: 'OpenRouter',
  voyage: 'Voyage AI',
  elevenlabs: 'ElevenLabs',
  deepgram: 'Deepgram',
  ollama: 'Ollama',
  azure: 'Azure OpenAI',
  vertex: 'Google Vertex AI',
  openai_compatible: 'OpenAI Compatible',
  dashscope: 'DashScope (Qwen)',
  minimax: 'MiniMax',
}

// All providers in display order
const ALL_PROVIDERS = [
  'openai', 'anthropic', 'google', 'groq', 'mistral', 'deepseek',
  'xai', 'openrouter', 'dashscope', 'minimax', 'voyage', 'elevenlabs', 'deepgram', 'ollama',
  'azure', 'vertex', 'openai_compatible',
]

// Documentation links
const PROVIDER_DOCS: Record<string, string> = {
  openai: 'https://platform.openai.com/api-keys',
  anthropic: 'https://console.anthropic.com/settings/keys',
  google: 'https://aistudio.google.com/app/apikey',
  groq: 'https://console.groq.com/keys',
  mistral: 'https://console.mistral.ai/api-keys/',
  deepseek: 'https://platform.deepseek.com/api_keys',
  xai: 'https://console.x.ai/',
  openrouter: 'https://openrouter.ai/keys',
  voyage: 'https://dash.voyageai.com/api-keys',
  elevenlabs: 'https://elevenlabs.io/app/settings/api-keys',
  deepgram: 'https://console.deepgram.com/',
  azure: 'https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/CognitiveServicesHub/~/OpenAI',
  vertex: 'https://cloud.google.com/vertex-ai/docs/start/cloud-environment',
  openai_compatible: 'https://github.com/lfnovo/open-notebook/blob/main/docs/5-CONFIGURATION/openai-compatible.md',
  dashscope: 'https://help.aliyun.com/zh/model-studio/getting-started/',
  minimax: 'https://platform.minimaxi.com/document/Guides',
}

const TYPE_ICONS: Record<ModelType, React.ReactNode> = {
  language: <MessageSquare className="h-3 w-3" />,
  embedding: <Code className="h-3 w-3" />,
  text_to_speech: <Volume2 className="h-3 w-3" />,
  speech_to_text: <Mic className="h-3 w-3" />,
}

const TYPE_COLORS: Record<ModelType, string> = {
  language: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  embedding: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300',
  text_to_speech: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  speech_to_text: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300',
}

const TYPE_COLOR_INACTIVE = 'bg-muted text-muted-foreground opacity-50'

const TYPE_LABELS: Record<ModelType, string> = {
  language: 'Language',
  embedding: 'Embedding',
  text_to_speech: 'TTS',
  speech_to_text: 'STT',
}

// =============================================================================
// Credential Form Dialog
// =============================================================================

function CredentialFormDialog({
  open,
  onOpenChange,
  provider,
  credential,
  initialBaseUrl,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  provider: string
  credential?: Credential | null
  initialBaseUrl?: string
}) {
  const { t } = useTranslation()
  const createCredential = useCreateCredential()
  const updateCredential = useUpdateCredential()
  const isEditing = !!credential
  const isSubmitting = createCredential.isPending || updateCredential.isPending

  const isVertex = provider === 'vertex'
  const isOllama = provider === 'ollama'
  const isOpenAICompatible = provider === 'openai_compatible'
  const requiresApiKey = !isVertex && !isOllama && !isOpenAICompatible

  const [name, setName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [clearApiKey, setClearApiKey] = useState(false)
  const [baseUrl, setBaseUrl] = useState('')
  const [showApiKey, setShowApiKey] = useState(false)
  const [project, setProject] = useState('')
  const [location, setLocation] = useState('')
  const [credentialsPath, setCredentialsPath] = useState('')
  const [numCtx, setNumCtx] = useState('')
  // Modalities
  const [modalities, setModalities] = useState<string[]>([])

  useEffect(() => {
    if (credential) {
      setName(credential.name || '')
      setBaseUrl(credential.base_url || '')
      setApiKey('')
      setClearApiKey(false)
      setProject(credential.project || '')
      setLocation(credential.location || '')
      setCredentialsPath(credential.credentials_path || '')
      setNumCtx(credential.num_ctx ? String(credential.num_ctx) : '')
      setModalities(credential.modalities || [])
    } else {
      setName('')
      setBaseUrl(initialBaseUrl || (provider === 'ollama' ? 'http://localhost:11434' : ''))
      setApiKey('')
      setClearApiKey(false)
      setProject('')
      setLocation('')
      setCredentialsPath('')
      setNumCtx('')
      setModalities(PROVIDER_MODALITIES[provider] || ['language'])
    }
  }, [credential, initialBaseUrl, provider])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    const onSuccess = () => {
      onOpenChange(false)
    }

    if (isEditing && credential) {
      const data: UpdateCredentialRequest = {}
      if (name !== credential.name) data.name = name
      if (isOpenAICompatible && clearApiKey) data.api_key = ''
      else if (apiKey.trim()) data.api_key = apiKey.trim()
      if (baseUrl !== (credential.base_url || '')) data.base_url = baseUrl || undefined
      if (JSON.stringify(modalities) !== JSON.stringify(credential.modalities)) data.modalities = modalities
      if (isVertex) {
        if (project !== (credential.project || '')) data.project = project.trim() || undefined
        if (location !== (credential.location || '')) data.location = location.trim() || undefined
        if (credentialsPath !== (credential.credentials_path || '')) data.credentials_path = credentialsPath.trim() || undefined
      }
      if (isOllama && numCtx !== (credential.num_ctx ? String(credential.num_ctx) : '')) {
        // empty clears the override (0 -> backend resets to default)
        data.num_ctx = numCtx.trim() ? Number(numCtx) : 0
      }
      updateCredential.mutate({ credentialId: credential.id, data }, { onSuccess })
    } else {
      const data: CreateCredentialRequest = {
        name: name || `${PROVIDER_DISPLAY_NAMES[provider] || provider} Config`,
        provider,
        modalities,
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl || undefined,
      }
      if (isVertex) {
        data.project = project.trim() || undefined
        data.location = location.trim() || undefined
        data.credentials_path = credentialsPath.trim() || undefined
      }
      if (isOllama && numCtx.trim()) {
        data.num_ctx = Number(numCtx)
      }
      createCredential.mutate(data, { onSuccess })
    }
  }

  const isValid = isEditing
    ? true
    : isVertex
      ? name.trim() !== '' && project.trim() !== '' && location.trim() !== ''
      : name.trim() !== '' && (!requiresApiKey || apiKey.trim() !== '')

  const docsUrl = PROVIDER_DOCS[provider]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {isEditing
              ? t('apiKeys.editConfig').replace('{provider}', PROVIDER_DISPLAY_NAMES[provider] || provider)
              : t('apiKeys.addConfig').replace('{provider}', PROVIDER_DISPLAY_NAMES[provider] || provider)}
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div className="space-y-2">
            <Label htmlFor="cred-name">{t('apiKeys.configName')}</Label>
            <input
              id="cred-name"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={`${PROVIDER_DISPLAY_NAMES[provider] || provider} Production`}
              disabled={isSubmitting}
            />
            <p className="text-xs text-muted-foreground">{t('apiKeys.configNameHint')}</p>
          </div>

          {/* Vertex fields */}
          {isVertex ? (
            <>
              <div className="space-y-2">
                <Label htmlFor="vertex-project">{t('apiKeys.vertexProject')}</Label>
                <input
                  id="vertex-project"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                  placeholder="my-gcp-project"
                  disabled={isSubmitting}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="vertex-location">{t('apiKeys.vertexLocation')}</Label>
                <input
                  id="vertex-location"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={location}
                  onChange={(e) => setLocation(e.target.value)}
                  placeholder="us-central1"
                  disabled={isSubmitting}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="vertex-creds">
                  {t('apiKeys.vertexCredentials')}
                  <span className="text-muted-foreground font-normal ml-1">({t('common.optional')})</span>
                </Label>
                <input
                  id="vertex-creds"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={credentialsPath}
                  onChange={(e) => setCredentialsPath(e.target.value)}
                  placeholder="/path/to/service-account.json"
                  disabled={isSubmitting}
                />
              </div>
            </>
          ) : (
            /* API Key */
            <div className="space-y-2">
              <Label htmlFor="api-key">
                {t('models.apiKey')}
                {!requiresApiKey && <span className="text-muted-foreground font-normal ml-1">({t('common.optional')})</span>}
              </Label>
              <div className="relative">
                <input
                  id="api-key"
                  type={showApiKey ? 'text' : 'password'}
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm pr-10"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={isEditing ? '••••••••••••' : 'sk-...'}
                  disabled={isSubmitting}
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey(!showApiKey)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground text-xs"
                  tabIndex={-1}
                >
                  {showApiKey ? 'Hide' : 'Show'}
                </button>
              </div>
              {isEditing && <p className="text-xs text-muted-foreground">{t('apiKeys.apiKeyEditHint')}</p>}
              {isEditing && isOpenAICompatible && credential?.has_api_key && (
                <label className="flex items-center gap-2 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={clearApiKey}
                    onChange={(e) => setClearApiKey(e.target.checked)}
                    disabled={isSubmitting}
                  />
                  Xóa khóa API đã lưu (LM Studio thường không yêu cầu khóa)
                </label>
              )}
              {docsUrl && (
                <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="text-xs text-primary hover:underline">
                  {t('apiKeys.getApiKey')} &rarr;
                </a>
              )}
            </div>
          )}

          {/* Base URL (non-Vertex) */}
          {!isVertex && (
            <div className="space-y-2">
              <Label htmlFor="base-url" className="text-muted-foreground">{t('apiKeys.baseUrl')}</Label>
              <input
                id="base-url"
                type="url"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={isOllama ? 'http://localhost:11434' : 'https://api.example.com/v1'}
                disabled={isSubmitting}
              />
              <p className="text-xs text-muted-foreground">{t('apiKeys.baseUrlOverrideHint')}</p>
              {isOpenAICompatible && (
                <p className="text-xs text-amber-700 dark:text-amber-300">
                  LM Studio: dùng <code>http://&lt;địa-chỉ-máy&gt;:1234/v1</code>. Nút lấy model sẽ đọc đúng mã model đang tải; không dùng đường dẫn REST <code>/api/v1</code>.
                </p>
              )}
            </div>
          )}

          {/* num_ctx (Ollama only) */}
          {isOllama && (
            <div className="space-y-2">
              <Label htmlFor="num-ctx" className="text-muted-foreground">
                {t('apiKeys.numCtx')}
                <span className="text-muted-foreground font-normal ml-1">({t('common.optional')})</span>
              </Label>
              <input
                id="num-ctx"
                type="number"
                min={1}
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={numCtx}
                onChange={(e) => setNumCtx(e.target.value)}
                placeholder="8192"
                disabled={isSubmitting}
              />
              <p className="text-xs text-muted-foreground">{t('apiKeys.numCtxHint')}</p>
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-4 border-t">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!isValid || isSubmitting}>
              {isSubmitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              {isEditing ? t('common.save') : t('apiKeys.addConfig')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// =============================================================================
// Model Discovery Dialog
// =============================================================================

function DiscoverModelsDialog({
  open,
  onOpenChange,
  credential,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  credential: Credential
}) {
  const { t } = useTranslation()
  const discoverModels = useDiscoverModels()
  const registerModels = useRegisterModels()
  const [discoveredModels, setDiscoveredModels] = useState<DiscoveredModel[]>([])
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set())
  const [hasDiscovered, setHasDiscovered] = useState(false)
  const [discoveryError, setDiscoveryError] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [customModelSelected, setCustomModelSelected] = useState(false)
  // Model type selector - default to credential's first modality
  const [selectedType, setSelectedType] = useState<ModelType>(
    (credential.modalities[0] as ModelType) || 'language'
  )

  useEffect(() => {
    if (open && !hasDiscovered) {
      setDiscoveryError(null)
      discoverModels.mutate(credential.id, {
        onSuccess: (result) => {
          const seen = new Set<string>()
          const unique = result.discovered.filter(m => {
            if (seen.has(m.name)) return false
            seen.add(m.name)
            return true
          })
          setDiscoveredModels(unique)
          setSelectedModels(new Set())
          setHasDiscovered(true)
        },
        onError: (error: unknown) => {
          setHasDiscovered(true)
          setDiscoveryError(formatApiError(error, 'Không thể tải danh sách mô hình từ nhà cung cấp.'))
        },
      })
    }
    if (!open) {
      setHasDiscovered(false)
      setDiscoveredModels([])
      setSelectedModels(new Set())
      setDiscoveryError(null)
      setSearchQuery('')
      setCustomModelSelected(false)
      setSelectedType((credential.modalities[0] as ModelType) || 'language')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally only fires on open/close
  }, [open])

  // Reset custom selection when search changes
  useEffect(() => {
    setCustomModelSelected(false)
  }, [searchQuery])

  // Filter discovered models by search query
  const filteredModels = useMemo(() => {
    if (!searchQuery.trim()) return discoveredModels
    const q = searchQuery.toLowerCase()
    return discoveredModels.filter(m => m.name.toLowerCase().includes(q))
  }, [discoveredModels, searchQuery])

  // Show custom model option when search doesn't exactly match any discovered model
  const showCustomOption = useMemo(() => {
    if (!searchQuery.trim()) return false
    const q = searchQuery.trim().toLowerCase()
    return !discoveredModels.some(m => m.name.toLowerCase() === q)
  }, [discoveredModels, searchQuery])

  const handleRegister = () => {
    const selected = discoveredModels
      .filter(m => selectedModels.has(m.name))
      .map(m => ({
        name: m.name,
        provider: m.provider,
        model_type: m.model_type || 'language',
      }))
    if (customModelSelected && showCustomOption) {
      selected.push({
        name: searchQuery.trim(),
        provider: credential.provider,
        model_type: selectedType,
      })
    }
    registerModels.mutate(
      { credentialId: credential.id, models: selected },
      { onSuccess: () => onOpenChange(false) }
    )
  }

  const totalSelected = selectedModels.size + (customModelSelected && showCustomOption ? 1 : 0)

  const toggleModel = (name: string) => {
    setSelectedModels(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const toggleAll = () => {
    const filteredNames = filteredModels.map(m => m.name)
    const allFilteredSelected = filteredNames.every(n => selectedModels.has(n))
    if (allFilteredSelected) {
      setSelectedModels(prev => {
        const next = new Set(prev)
        filteredNames.forEach(n => next.delete(n))
        return next
      })
    } else {
      setSelectedModels(prev => {
        const next = new Set(prev)
        filteredNames.forEach(n => next.add(n))
        return next
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="h-[min(820px,88vh)] w-[calc(100vw-2rem)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>
            {t('models.discoverModels')} - {PROVIDER_DISPLAY_NAMES[credential.provider] || credential.provider}
          </DialogTitle>
          <DialogDescription>
            {credential.name}
          </DialogDescription>
        </DialogHeader>

        {discoverModels.isPending ? (
          <div className="flex items-center justify-center py-12">
            <LoadingSpinner size="lg" />
          </div>
        ) : discoveryError ? (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{discoveryError}</AlertDescription>
          </Alert>
        ) : (
          <div className="flex min-h-0 flex-col gap-4 overflow-hidden">
            {/* Model type selector */}
            <div className="space-y-2">
              <Label>{t('models.modelType')}</Label>
              <Select value={selectedType} onValueChange={(v) => setSelectedType(v as ModelType)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(PROVIDER_MODALITIES[credential.provider] || credential.modalities as ModelType[]).map(type => (
                    <SelectItem key={type} value={type}>
                      <div className="flex items-center gap-2">
                        {TYPE_ICONS[type]}
                        {TYPE_LABELS[type]}
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">{t('models.modelTypeHint')}</p>
            </div>

            {/* Search input */}
            <input
              type="text"
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm placeholder:text-muted-foreground"
              placeholder={t('models.searchOrAddModel')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />

            {/* Select all / count (only when there are discovered models to select) */}
            {filteredModels.length > 0 && (
              <div className="flex items-center justify-between">
                <Button variant="outline" size="sm" onClick={toggleAll}>
                  {filteredModels.every(m => selectedModels.has(m.name)) ? t('common.remove') : t('common.addSelected')}
                  {' '}({selectedModels.size}/{filteredModels.length})
                </Button>
              </div>
            )}

            {/* Model list */}
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto rounded-md border bg-muted/20 p-2 pr-3">
              {filteredModels.map((model) => (
                <label
                  key={model.name}
                  className="grid min-w-0 cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-0.5 rounded px-3 py-2 text-sm hover:bg-muted sm:grid-cols-[auto_minmax(220px,0.9fr)_minmax(0,1.1fr)]"
                >
                  <input
                    type="checkbox"
                    checked={selectedModels.has(model.name)}
                    onChange={() => toggleModel(model.name)}
                    className="row-span-2 rounded sm:row-span-1"
                  />
                  <span className="min-w-0 break-words font-medium">
                    {model.name}
                    <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[10px] font-normal text-muted-foreground">
                      {TYPE_LABELS[(model.model_type || 'language') as ModelType] || model.model_type || 'language'}
                    </span>
                  </span>
                  {model.description && model.description !== model.name && (
                    <span className="min-w-0 break-words text-xs text-muted-foreground sm:text-sm">{model.description}</span>
                  )}
                </label>
              ))}

              {/* Custom model option */}
              {showCustomOption && (
                <label className={`flex cursor-pointer items-center gap-2 rounded px-3 py-2 text-sm hover:bg-muted${filteredModels.length > 0 ? ' mt-1 border-t pt-3' : ''}`}>
                  <input
                    type="checkbox"
                    checked={customModelSelected}
                    onChange={() => setCustomModelSelected(prev => !prev)}
                    className="rounded"
                  />
                  <Plus className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                  <span className="truncate">
                    {t('models.addCustomModel').replace('{name}', searchQuery.trim())}
                  </span>
                </label>
              )}

              {filteredModels.length === 0 && !showCustomOption && (
                <p className="text-center py-4 text-muted-foreground text-sm">{t('models.noModelsFound')}</p>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleRegister}
            disabled={totalSelected === 0 || registerModels.isPending}
          >
            {registerModels.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
            {t('common.add')} ({totalSelected})
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// =============================================================================
// Delete Credential Dialog
// =============================================================================

function DeleteCredentialDialog({
  open,
  onOpenChange,
  credential,
  allCredentials,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  credential: Credential
  allCredentials: Credential[]
}) {
  const { t } = useTranslation()
  const deleteCredential = useDeleteCredential()
  const [migrateToId, setMigrateToId] = useState<string>('')

  const otherCredentials = allCredentials.filter(
    c => c.id !== credential.id && c.provider === credential.provider
  )

  const handleDeleteWithModels = () => {
    deleteCredential.mutate(
      { credentialId: credential.id, options: { delete_models: true } },
      { onSuccess: () => onOpenChange(false) }
    )
  }

  const handleMigrate = () => {
    if (!migrateToId) return
    deleteCredential.mutate(
      { credentialId: credential.id, options: { migrate_to: migrateToId } },
      { onSuccess: () => onOpenChange(false) }
    )
  }

  const handleDeleteOnly = () => {
    deleteCredential.mutate(
      { credentialId: credential.id },
      { onSuccess: () => onOpenChange(false) }
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('apiKeys.deleteConfig')}</DialogTitle>
          <DialogDescription>
            {t('apiKeys.deleteConfigConfirm').replace('{name}', credential.name)}
          </DialogDescription>
        </DialogHeader>

        {credential.model_count > 0 && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              This credential has {credential.model_count} linked model(s).
              {otherCredentials.length > 0 && (
                <div className="mt-2">
                  <Label>Migrate models to:</Label>
                  <Select value={migrateToId} onValueChange={setMigrateToId}>
                    <SelectTrigger className="mt-1">
                      <SelectValue placeholder={t("common.selectCredential")} />
                    </SelectTrigger>
                    <SelectContent>
                      {otherCredentials.map(c => (
                        <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </AlertDescription>
          </Alert>
        )}

        <DialogFooter className="flex-col sm:flex-row gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          {credential.model_count > 0 && migrateToId && (
            <Button onClick={handleMigrate} disabled={deleteCredential.isPending}>
              {deleteCredential.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              Migrate & Delete
            </Button>
          )}
          <Button
            variant="destructive"
            onClick={credential.model_count > 0 ? handleDeleteWithModels : handleDeleteOnly}
            disabled={deleteCredential.isPending}
          >
            {deleteCredential.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
            {credential.model_count > 0 ? 'Delete with Models' : t('common.delete')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// =============================================================================
// Credential Card (shows credential + its models)
// =============================================================================

function CredentialItem({
  credential,
  models,
  defaults,
  allCredentials,
}: {
  credential: Credential
  models: Model[]
  defaults: ModelDefaults | null
  allCredentials: Credential[]
}) {
  const { t } = useTranslation()
  const { testCredential, isPending: isTestPending, testResults } = useTestCredential()
  const { testModel, isPending: isModelTestPending, testingModelId, testResult: modelTestResult, testedModelName, clearResult: clearModelTestResult } = useTestModel()
  const deleteModel = useDeleteModel()
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [discoverOpen, setDiscoverOpen] = useState(false)
  // Full credential data needed for edit form
  const { data: fullCredential } = useCredential(editOpen ? credential.id : '')

  const linkedModels = models.filter(m => m.credential === credential.id)
  const usableLinkedModels = linkedModels.filter(isSupportedProviderModality)
  const invalidLinkedModels = linkedModels.filter((model) => !isSupportedProviderModality(model))
  const activeTypes = new Set(usableLinkedModels.map(m => m.type))
  const testResult = testResults[credential.id]

  // Extract translations used in model badge loops to avoid excessive Proxy accesses
  const testModelLabel = t('models.testModel')
  const deleteModelLabel = t('models.deleteModel')

  // Check which models are defaults
  const defaultSlots: Record<string, string> = {}
  if (defaults) {
    const slotMap: Record<string, string | null | undefined> = {
      'Chat': defaults.default_chat_model,
      'Tools': defaults.default_tools_model,
      'Large Ctx': defaults.large_context_model,
      'Embedding': defaults.default_embedding_model,
      'STT': defaults.default_speech_to_text_model,
    }
    for (const [slot, modelId] of Object.entries(slotMap)) {
      if (modelId) defaultSlots[modelId] = slot
    }
  }

  return (
    <>
      <div className="border rounded-lg p-3 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-medium truncate">{credential.name}</span>
            <div className="flex gap-1">
              {credential.modalities.map(mod => (
                <Badge
                  key={mod}
                  variant="secondary"
                  className={`text-[10px] gap-0.5 px-1 py-0 ${activeTypes.has(mod as ModelType) ? (TYPE_COLORS[mod as ModelType] || '') : TYPE_COLOR_INACTIVE}`}
                >
                  {TYPE_ICONS[mod as ModelType]}
                  <span className="hidden sm:inline">{TYPE_LABELS[mod as ModelType] || mod}</span>
                </Badge>
              ))}
            </div>
            {credential.has_api_key && (
              <Badge variant="outline" className="text-[10px]">
                <Key className="h-2.5 w-2.5 mr-0.5" />
                Key
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {testResult && (
              testResult.success
                ? <Check className="h-4 w-4 text-emerald-500" />
                : <X className="h-4 w-4 text-destructive" />
            )}
            <Button
              variant="ghost" size="sm"
              onClick={() => testCredential(credential.id)}
              disabled={isTestPending || !!credential.decryption_error}
              title={t('apiKeys.testConnection')}
            >
              {isTestPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
              <span className="hidden sm:inline text-xs">Test</span>
            </Button>
            <Button
              variant="ghost" size="sm"
              onClick={() => setDiscoverOpen(true)}
              disabled={!!credential.decryption_error}
              title={t('apiKeys.syncModels')}
            >
              <Bot className="h-4 w-4" />
              <span className="hidden sm:inline text-xs">Models</span>
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setEditOpen(true)} disabled={!!credential.decryption_error} title={t('common.edit')}>
              <Edit className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost" size="sm"
              onClick={() => setDeleteOpen(true)}
              className="text-destructive hover:text-destructive hover:bg-destructive/10"
              title={t('common.delete')}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>

        {/* Decryption error warning */}
        {credential.decryption_error && (
          <Alert className="border-amber-500/50 bg-amber-50 dark:bg-amber-950/20">
            <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
            <AlertTitle className="text-amber-800 dark:text-amber-200">{t('apiKeys.decryptionError')}</AlertTitle>
            <AlertDescription className="text-amber-700 dark:text-amber-300 text-sm">
              {t('apiKeys.decryptionErrorDescription')}
            </AlertDescription>
          </Alert>
        )}

        {invalidLinkedModels.length > 0 && (
          <Alert className="border-amber-500/50 bg-amber-50 dark:bg-amber-950/20">
            <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400" />
            <AlertDescription className="text-sm text-amber-800 dark:text-amber-200">
              {invalidLinkedModels.length} model cũ sai loại đã bị vô hiệu hóa an toàn. Hãy xóa model này và lấy lại danh sách model từ nhà cung cấp.
            </AlertDescription>
          </Alert>
        )}

        {/* Linked models grouped by type */}
        {usableLinkedModels.length > 0 && (
          <div className="space-y-1.5 pt-1">
            {(['language', 'embedding', 'text_to_speech', 'speech_to_text'] as ModelType[])
              .filter(type => usableLinkedModels.some(m => m.type === type))
              .map(type => (
                <div key={type} className="flex items-start gap-1.5">
                  <Badge
                    variant="outline"
                    className={`text-[10px] gap-0.5 px-1 py-0 shrink-0 mt-0.5 ${TYPE_COLORS[type]}`}
                  >
                    {TYPE_ICONS[type]}
                    {TYPE_LABELS[type]}
                  </Badge>
                  <div className="flex flex-wrap gap-1">
                    {usableLinkedModels.filter(m => m.type === type).map(model => {
                      const defaultSlot = defaultSlots[model.id]
                      return (
                        <Badge
                          key={model.id}
                          variant={defaultSlot ? 'default' : 'secondary'}
                          className="text-xs gap-1 pr-0.5 group/model"
                        >
                          {model.name}
                          {defaultSlot && <span className="ml-0.5 opacity-75">({defaultSlot})</span>}
                          <button
                            className="ml-0.5 opacity-0 group-hover/model:opacity-60 hover:!opacity-100 transition-opacity"
                            onClick={() => testModel(model.id, model.name)}
                            disabled={isModelTestPending && testingModelId === model.id}
                            title={testModelLabel}
                          >
                            {isModelTestPending && testingModelId === model.id
                              ? <Loader2 className="h-3 w-3 animate-spin" />
                              : <Plug className="h-3 w-3" />
                            }
                          </button>
                          <button
                            className="opacity-0 group-hover/model:opacity-60 hover:!opacity-100 hover:text-destructive transition-opacity"
                            onClick={() => deleteModel.mutate(model.id)}
                            title={deleteModelLabel}
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </Badge>
                      )
                    })}
                  </div>
                </div>
              ))}
          </div>
        )}


      </div>

      {/* Edit dialog */}
      {editOpen && (
        <CredentialFormDialog
          open={editOpen}
          onOpenChange={setEditOpen}
          provider={credential.provider}
          credential={fullCredential || credential}
        />
      )}

      {/* Delete dialog */}
      {deleteOpen && (
        <DeleteCredentialDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          credential={credential}
          allCredentials={allCredentials}
        />
      )}

      {/* Discover models dialog */}
      {discoverOpen && (
        <DiscoverModelsDialog
          open={discoverOpen}
          onOpenChange={setDiscoverOpen}
          credential={credential}
        />
      )}

      {/* Model test result dialog */}
      <ModelTestResultDialog
        open={modelTestResult !== null}
        onOpenChange={(open) => { if (!open) clearModelTestResult() }}
        result={modelTestResult}
        modelName={testedModelName}
      />
    </>
  )
}

// =============================================================================
// Provider Section (shows all credentials for a provider)
// =============================================================================

function ProviderSection({
  provider,
  credentials,
  models,
  defaults,
  allCredentials,
  encryptionReady,
}: {
  provider: string
  credentials: Credential[]
  models: Model[]
  defaults: ModelDefaults | null
  allCredentials: Credential[]
  encryptionReady: boolean
}) {
  const { t } = useTranslation()
  const [addOpen, setAddOpen] = useState(false)

  const displayName = PROVIDER_DISPLAY_NAMES[provider] || provider
  const modalities = PROVIDER_MODALITIES[provider] || ['language']
  const hasCredentials = credentials.length > 0

  // Models linked to any credential of this provider
  const providerModels = models.filter(m =>
    credentials.some(c => c.id === m.credential) && isSupportedProviderModality(m)
  )
  const activeTypes = new Set(providerModels.map(m => m.type))

  return (
    <Card className={!hasCredentials ? 'opacity-80' : undefined}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 flex-wrap">
            <CardTitle className="text-lg capitalize">{displayName}</CardTitle>
            <div className="flex items-center gap-1">
              {modalities.map((type) => (
                <Badge
                  key={type}
                  variant="secondary"
                  className={`text-xs gap-1 ${activeTypes.has(type) ? TYPE_COLORS[type] : TYPE_COLOR_INACTIVE}`}
                >
                  {TYPE_ICONS[type]}
                  <span className="hidden sm:inline">{TYPE_LABELS[type]}</span>
                </Badge>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {hasCredentials ? (
              <Badge className="bg-emerald-100 text-emerald-700 hover:bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-300">
                <Check className="mr-1 h-3 w-3" />
                {t('apiKeys.configured')}
              </Badge>
            ) : (
              <Badge variant="outline" className="text-muted-foreground border-dashed">
                <X className="mr-1 h-3 w-3" />
                {t('apiKeys.notConfigured')}
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {credentials.map(cred => (
          <CredentialItem
            key={cred.id}
            credential={cred}
            models={models}
            defaults={defaults}
            allCredentials={allCredentials}
          />
        ))}

        <Button
          variant="outline"
          size="sm"
          onClick={() => setAddOpen(true)}
          className="w-full gap-2"
          disabled={!encryptionReady}
        >
          <Plus className="h-4 w-4" />
          {t('apiKeys.addConfig')}
        </Button>
      </CardContent>

      {addOpen && (
        <CredentialFormDialog
          open={addOpen}
          onOpenChange={setAddOpen}
          provider={provider}
        />
      )}
    </Card>
  )
}

// =============================================================================
// Default Models Section
// =============================================================================

function DefaultModelSelectors({
  models,
  defaults,
}: {
  models: Model[]
  defaults: ModelDefaults
}) {
  const { t } = useTranslation()
  const updateDefaults = useUpdateModelDefaults()
  const autoAssign = useAutoAssignDefaults()
  const { setValue, watch } = useForm<ModelDefaults>({ defaultValues: defaults })
  const generatedId = useId()

  const [showEmbeddingDialog, setShowEmbeddingDialog] = useState(false)
  const [pendingEmbeddingChange, setPendingEmbeddingChange] = useState<{
    key: keyof ModelDefaults; value: string; oldModelId?: string; newModelId?: string
  } | null>(null)

  useEffect(() => {
    if (defaults) {
      Object.entries(defaults).forEach(([key, value]) => {
        setValue(key as keyof ModelDefaults, value)
      })
    }
  }, [defaults, setValue])

  interface DefaultConfig {
    key: keyof ModelDefaults
    label: string
    description: string
    modelType: ModelType
    required?: boolean
    id: string
  }

  const primaryConfigs: DefaultConfig[] = [
    { key: 'default_chat_model', label: t('models.chatModelLabel'), description: t('models.chatModelDesc'), modelType: 'language', required: true, id: `${generatedId}-chat` },
    { key: 'default_embedding_model', label: t('models.embeddingModelLabel'), description: t('models.embeddingModelDesc'), modelType: 'embedding', required: true, id: `${generatedId}-embed` },
    { key: 'default_speech_to_text_model', label: t('models.sttModelLabel'), description: t('models.sttModelDesc'), modelType: 'speech_to_text', id: `${generatedId}-stt` },
  ]

  const advancedConfigs: DefaultConfig[] = [
    { key: 'default_tools_model', label: t('models.toolsModelLabel'), description: t('models.toolsModelDesc'), modelType: 'language', id: `${generatedId}-tools` },
    { key: 'large_context_model', label: t('models.largeContextModelLabel'), description: t('models.largeContextModelDesc'), modelType: 'language', id: `${generatedId}-large` },
  ]

  const defaultConfigs = [...primaryConfigs, ...advancedConfigs]

  const handleChange = (key: keyof ModelDefaults, value: string) => {
    if (key === 'default_embedding_model') {
      const current = defaults[key]
      if (current && current !== value) {
        setPendingEmbeddingChange({ key, value, oldModelId: current, newModelId: value })
        setShowEmbeddingDialog(true)
        return
      }
    }
    updateDefaults.mutate({ [key]: value || null })
  }

  const handleConfirmEmbeddingChange = () => {
    if (pendingEmbeddingChange) {
      updateDefaults.mutate({ [pendingEmbeddingChange.key]: pendingEmbeddingChange.value || null })
      setPendingEmbeddingChange(null)
    }
  }

  const getModelsForType = (type: ModelType) => models.filter(
    m => m.type === type && isSupportedProviderModality(m)
  )

  const missingRequired = defaultConfigs
    .filter(c => {
      if (!c.required) return false
      const value = defaults[c.key]
      if (!value) return true
      return !getModelsForType(c.modelType).some(m => m.id === value)
    })
    .map(c => c.label)

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('models.defaultAssignments')}</CardTitle>
        <CardDescription>{t('models.defaultAssignmentsDesc')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {missingRequired.length > 0 && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription className="flex flex-wrap items-center justify-between gap-4">
              <div className="min-w-0 flex-1 space-y-1">
                <p>{t('models.missingRequiredModels').replace('{models}', missingRequired.join(', '))}</p>
                <p className="text-sm">Đây là cấu hình model mặc định cho các chức năng dùng chung, không phải kết quả kiểm tra kho tra cứu pháp luật. Kho pháp luật có dịch vụ tìm kiếm riêng; xem trạng thái vận hành tại trang quản trị.</p>
              </div>
              <Button
                variant="outline" size="sm"
                onClick={() => autoAssign.mutate()}
                disabled={autoAssign.isPending}
                className="shrink-0 gap-1.5"
              >
                {autoAssign.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wand2 className="h-3.5 w-3.5" />}
                {autoAssign.isPending ? t('models.autoAssigning') : t('models.autoAssign')}
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {/* Primary runtime models. TTS has no product caller, so it is not exposed as a default. */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {primaryConfigs.map(config => {
            const available = getModelsForType(config.modelType)
            const currentValue = watch(config.key) || undefined
            const isValid = currentValue && available.some(m => m.id === currentValue)

            return (
              <div key={config.key} className="space-y-1">
                <Label htmlFor={config.id} className="text-xs">
                  {config.label}
                  {config.required && <span className="text-destructive ml-0.5">*</span>}
                </Label>
                <div className="flex gap-1">
                  <Select
                    value={currentValue || ""}
                    onValueChange={(v) => handleChange(config.key, v)}
                  >
                    <SelectTrigger
                      id={config.id}
                      className={`h-8 text-xs ${config.required && !isValid && available.length > 0 ? 'border-destructive' : ''}`}
                    >
                      <SelectValue placeholder={
                        config.required && !isValid && available.length > 0
                          ? t('models.requiredModelPlaceholder')
                          : t('models.selectModelPlaceholder')
                      } />
                    </SelectTrigger>
                    <SelectContent>
                      {available.sort((a, b) => a.name.localeCompare(b.name)).map(model => (
                        <SelectItem key={model.id} value={model.id}>
                          <div className="flex items-center justify-between w-full">
                            <span>{model.name}</span>
                            <span className="text-xs text-muted-foreground ml-2">{model.provider}</span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {!config.required && currentValue && (
                    <Button variant="ghost" size="icon" onClick={() => handleChange(config.key, "")} className="h-8 w-8 shrink-0">
                      <X className="h-3 w-3" />
                    </Button>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {/* Optional models: tools and large-context use cases. */}
        <div className="border-t pt-3">
          <p className="text-xs text-muted-foreground mb-3">Mô hình bổ sung</p>
            <div className="grid gap-3 sm:grid-cols-3">
              {advancedConfigs.map(config => {
                const available = getModelsForType(config.modelType)
                const currentValue = watch(config.key) || undefined
                const isValid = currentValue && available.some(m => m.id === currentValue)

                return (
                  <div key={config.key} className="space-y-1">
                    <Label htmlFor={config.id} className="text-xs">
                      {config.label}
                      {config.required && <span className="text-destructive ml-0.5">*</span>}
                    </Label>
                    <div className="flex gap-1">
                      <Select
                        value={currentValue || ""}
                        onValueChange={(v) => handleChange(config.key, v)}
                      >
                        <SelectTrigger
                          id={config.id}
                          className={`h-8 text-xs ${config.required && !isValid && available.length > 0 ? 'border-destructive' : ''}`}
                        >
                          <SelectValue placeholder={
                            config.required && !isValid && available.length > 0
                              ? t('models.requiredModelPlaceholder')
                              : t('models.selectModelPlaceholder')
                          } />
                        </SelectTrigger>
                        <SelectContent>
                          {available.sort((a, b) => a.name.localeCompare(b.name)).map(model => (
                            <SelectItem key={model.id} value={model.id}>
                              <div className="flex items-center justify-between w-full">
                                <span>{model.name}</span>
                                <span className="text-xs text-muted-foreground ml-2">{model.provider}</span>
                              </div>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {!config.required && currentValue && (
                        <Button variant="ghost" size="icon" onClick={() => handleChange(config.key, "")} className="h-8 w-8 shrink-0">
                          <X className="h-3 w-3" />
                        </Button>
                      )}
                    </div>
                    <p className="text-[10px] text-muted-foreground leading-tight">{config.description}</p>
                  </div>
                )
              })}
            </div>
        </div>
      </CardContent>

      <EmbeddingModelChangeDialog
        open={showEmbeddingDialog}
        onOpenChange={(open) => { if (!open) { setPendingEmbeddingChange(null); setShowEmbeddingDialog(false) } }}
        onConfirm={handleConfirmEmbeddingChange}
        oldModelName={pendingEmbeddingChange?.oldModelId ? models.find(m => m.id === pendingEmbeddingChange.oldModelId)?.name : undefined}
        newModelName={pendingEmbeddingChange?.newModelId ? models.find(m => m.id === pendingEmbeddingChange.newModelId)?.name : undefined}
      />
    </Card>
  )
}

// =============================================================================
// Guided setup for non-technical administrators
// =============================================================================

function EasyModelSetup({
  credentials,
  models,
  defaults,
  encryptionReady,
}: {
  credentials: Credential[]
  models: Model[]
  defaults: ModelDefaults
  encryptionReady: boolean
}) {
  const [runMode, setRunMode] = useState<'local' | 'cloud'>('local')
  const [cloudProvider, setCloudProvider] = useState('google')
  const [localConnection, setLocalConnection] = useState<'same-machine' | 'docker'>('same-machine')
  const [configOpen, setConfigOpen] = useState(false)
  const [discoverOpen, setDiscoverOpen] = useState(false)
  const testCredential = useTestCredential()
  const updateDefaults = useUpdateModelDefaults()
  const [showEmbeddingDialog, setShowEmbeddingDialog] = useState(false)
  const [pendingDefaults, setPendingDefaults] = useState<Partial<ModelDefaults> | null>(null)

  const provider = runMode === 'local' ? 'ollama' : cloudProvider
  const credential = credentials.find((item) => item.provider === provider) || null
  const providerModels = models.filter(
    (item) => item.provider === provider && isSupportedProviderModality(item)
  )
  const testResult = credential ? testCredential.testResults[credential.id] : undefined
  const readiness = providerSetupReadiness(models, provider, {
    credentialConfigured: Boolean(credential),
    connectionPassed: Boolean(testResult?.success),
  })
  const { chatModel, embeddingModel } = readiness
  const currentChat = models.find((item) => item.id === defaults.default_chat_model)
  const currentEmbedding = models.find((item) => item.id === defaults.default_embedding_model)
  const defaultsReady = Boolean(
    currentChat?.type === 'language' &&
    currentEmbedding?.type === 'embedding' &&
    isSupportedProviderModality(currentChat) &&
    isSupportedProviderModality(currentEmbedding)
  )
  const providerReady = readiness.ready
  const selectedProviderActive = Boolean(
    defaultsReady && currentChat?.provider === provider && currentEmbedding?.provider === provider
  )
  const initialBaseUrl = runMode === 'local'
    ? localOllamaBaseUrl(localConnection)
    : undefined

  const applySelectedDefaults = (nextDefaults: Partial<ModelDefaults>) => {
    updateDefaults.mutate(nextDefaults)
    setPendingDefaults(null)
  }

  const activateSelectedProvider = () => {
    if (!chatModel || !embeddingModel) return
    const nextDefaults = {
      default_chat_model: chatModel.id,
      default_embedding_model: embeddingModel.id,
    }
    if (defaults.default_embedding_model && defaults.default_embedding_model !== embeddingModel.id) {
      setPendingDefaults(nextDefaults)
      setShowEmbeddingDialog(true)
      return
    }
    applySelectedDefaults(nextDefaults)
  }

  return (
    <Card className="border-primary/30 bg-primary/[0.02]">
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Wand2 className="h-5 w-5 text-primary" /> Thiết lập AI có hướng dẫn</CardTitle>
        <CardDescription>Thiết lập model mặc định cho các chức năng dùng chung theo 4 bước. Trình thiết lập này không thay đổi model tìm kiếm riêng của kho pháp luật và không dùng để kết luận kho đó sẵn sàng hay gặp lỗi.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-3 md:grid-cols-2">
          <button
            type="button"
            onClick={() => setRunMode('local')}
            className={`rounded-lg border p-4 text-left transition-colors ${runMode === 'local' ? 'border-primary bg-primary/5' : 'hover:bg-muted'}`}
          >
            <span className="flex items-center gap-2 font-medium"><Laptop className="h-5 w-5" /> Chạy local bằng Ollama</span>
            <span className="mt-1 block text-xs text-muted-foreground">Không cần khóa kết nối; dữ liệu không gửi tới nhà cung cấp AI.</span>
          </button>
          <button
            type="button"
            onClick={() => setRunMode('cloud')}
            className={`rounded-lg border p-4 text-left transition-colors ${runMode === 'cloud' ? 'border-primary bg-primary/5' : 'hover:bg-muted'}`}
          >
            <span className="flex items-center gap-2 font-medium"><Cloud className="h-5 w-5" /> Dùng dịch vụ đám mây</span>
            <span className="mt-1 block text-xs text-muted-foreground">Dễ triển khai cho nhiều người dùng; cần khóa kết nối của nhà cung cấp.</span>
          </button>
        </div>

        <div className="grid gap-4 rounded-lg border bg-background p-4 lg:grid-cols-4">
          <div className="space-y-2">
            <div className="flex items-center gap-2"><Badge variant="secondary">1</Badge><Label>Chọn kết nối</Label></div>
            {runMode === 'local' ? (
              <Select value={localConnection} onValueChange={(value) => setLocalConnection(value as 'same-machine' | 'docker')}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="same-machine">Ollama cùng máy</SelectItem>
                  <SelectItem value="docker">Ứng dụng chạy Docker</SelectItem>
                </SelectContent>
              </Select>
            ) : (
              <Select value={cloudProvider} onValueChange={setCloudProvider}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {['openai', 'google', 'mistral'].map((item) => (
                    <SelectItem key={item} value={item}>{PROVIDER_DISPLAY_NAMES[item]}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <p className="text-xs text-muted-foreground">{runMode === 'local' ? initialBaseUrl : 'Khóa kết nối được mã hóa trước khi lưu.'}</p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-2"><Badge variant="secondary">2</Badge><Label>Lưu cấu hình</Label></div>
            <Button className="w-full" variant={credential ? 'outline' : 'default'} disabled={!encryptionReady} onClick={() => setConfigOpen(true)}>
              {credential ? <Edit className="mr-2 h-4 w-4" /> : <Plus className="mr-2 h-4 w-4" />}
              {credential ? 'Sửa kết nối' : 'Tạo kết nối'}
            </Button>
            <p className="text-xs text-muted-foreground">{credential ? `Đã có: ${credential.name}` : 'Chưa có cấu hình cho lựa chọn này.'}</p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-2"><Badge variant="secondary">3</Badge><Label>Kiểm tra và lấy model</Label></div>
            <div className="flex gap-2">
              <Button className="flex-1" variant="outline" disabled={!credential || testCredential.isPending} onClick={() => credential && testCredential.testCredential(credential.id)}>
                {testCredential.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}
              </Button>
              <Button className="flex-1" variant="outline" disabled={!credential} onClick={() => setDiscoverOpen(true)}><Bot className="h-4 w-4" /></Button>
            </div>
            <p className={`text-xs ${testResult?.success ? 'text-green-700 dark:text-green-400' : 'text-muted-foreground'}`}>
              {testResult ? (testResult.success ? 'Kết nối thành công.' : 'Kết nối chưa thành công.') : `${providerModels.length} model đã đăng ký.`}
            </p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-2"><Badge variant="secondary">4</Badge><Label>Kích hoạt mặc định</Label></div>
            <Button className="w-full" variant={selectedProviderActive ? 'outline' : 'default'} disabled={!providerReady || updateDefaults.isPending} onClick={activateSelectedProvider}>
              {updateDefaults.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
              {selectedProviderActive ? 'Đang được sử dụng' : 'Dùng model đã chọn'}
            </Button>
            <p className="text-xs text-muted-foreground">
              {selectedProviderActive
                ? 'Kết nối này đang cấp cả model trả lời và embedding.'
                : providerReady
                  ? 'Đã đủ model trả lời và embedding để kích hoạt.'
                  : readiness.blockers.includes('connection_not_verified')
                    ? 'Cần kiểm tra kết nối thành công trước khi kích hoạt.'
                    : 'Để kích hoạt cặp model qua trình thiết lập này, cần đăng ký một model trả lời và một model embedding thật. Điều này không chặn kho pháp luật đang dùng dịch vụ tìm kiếm riêng.'}
            </p>
          </div>
        </div>

        <Alert>
          <AlertCircle className="h-4 w-4" />
          <AlertDescription><strong>Mô hình trả lời</strong> dùng để hiểu và soạn câu trả lời. <strong>Mô hình tạo dữ liệu tìm kiếm</strong> dùng để biểu diễn văn bản và câu hỏi theo ngữ nghĩa. Không đổi mô hình tìm kiếm của kho đang chạy nếu chưa tạo lại dữ liệu tra cứu có kiểm soát.</AlertDescription>
        </Alert>

        <CredentialFormDialog
          open={configOpen}
          onOpenChange={setConfigOpen}
          provider={provider}
          credential={credential}
          initialBaseUrl={initialBaseUrl}
        />
        {credential && (
          <DiscoverModelsDialog
            open={discoverOpen}
            onOpenChange={setDiscoverOpen}
            credential={credential}
          />
        )}
        <EmbeddingModelChangeDialog
          open={showEmbeddingDialog}
          onOpenChange={(open) => {
            setShowEmbeddingDialog(open)
            if (!open) setPendingDefaults(null)
          }}
          onConfirm={() => pendingDefaults && applySelectedDefaults(pendingDefaults)}
          oldModelName={currentEmbedding?.name}
          newModelName={embeddingModel?.name}
        />
      </CardContent>
    </Card>
  )
}

// =============================================================================
// Main Page
// =============================================================================

export default function ApiKeysPage() {
  const { t } = useTranslation()

  // Data
  const { data: credentials, isLoading: credentialsLoading } = useCredentials()
  const { data: models, isLoading: modelsLoading } = useModels()
  const { data: defaults, isLoading: defaultsLoading } = useModelDefaults()
  const { data: credentialStatus } = useCredentialStatus()
  const { data: envStatus } = useEnvStatus()

  const encryptionReady = credentialStatus?.encryption_configured ?? true

  // Group credentials by provider
  const credentialsByProvider = useMemo(() => {
    const grouped: Record<string, Credential[]> = {}
    for (const provider of ALL_PROVIDERS) {
      grouped[provider] = []
    }
    if (credentials) {
      for (const cred of credentials) {
        if (!grouped[cred.provider]) grouped[cred.provider] = []
        grouped[cred.provider].push(cred)
      }
    }
    return grouped
  }, [credentials])

  // Providers needing migration
  const providersToMigrate = useMemo(() => {
    if (!envStatus || !credentialStatus) return []
    const providers: string[] = []
    for (const provider in envStatus) {
      if (envStatus[provider] && credentialStatus.source[provider] === 'environment') {
        providers.push(provider)
      }
    }
    return providers
  }, [envStatus, credentialStatus])

  // Sort: configured providers first
  const sortedProviders = useMemo(() => {
    return [...ALL_PROVIDERS].sort((a, b) => {
      const aHas = (credentialsByProvider[a]?.length || 0) > 0 ? 1 : 0
      const bHas = (credentialsByProvider[b]?.length || 0) > 0 ? 1 : 0
      return bHas - aHas
    })
  }, [credentialsByProvider])

  const isLoading = credentialsLoading || modelsLoading || defaultsLoading

  if (isLoading) {
    return (
      <AppShell>
        <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3" aria-busy="true">
          <h1 className="text-2xl font-bold">Đang tải cấu hình mô hình và khóa API</h1>
          <LoadingSpinner size="lg" />
          <p className="text-sm text-muted-foreground">Vui lòng chờ trong giây lát.</p>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="p-6 space-y-6">
          {/* Header */}
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Key className="h-6 w-6" />
              {t('apiKeys.title')}
            </h1>
            <p className="text-muted-foreground mt-1">{t('apiKeys.description')}</p>
          </div>

          {/* Encryption warning */}
          {!encryptionReady && (
            <Alert className="border-red-500/50 bg-red-50 dark:bg-red-950/20">
              <ShieldAlert className="h-4 w-4 text-red-600 dark:text-red-400" />
              <AlertTitle className="text-red-800 dark:text-red-200">{t('apiKeys.encryptionRequired')}</AlertTitle>
              <AlertDescription className="text-red-700 dark:text-red-300">
                <code className="text-xs bg-red-100 dark:bg-red-900/30 px-1 py-0.5 rounded">
                  {t('apiKeys.encryptionRequiredDescription')}
                </code>
              </AlertDescription>
            </Alert>
          )}

          {/* Migration banner */}
          {encryptionReady && <MigrationBanner providersToMigrate={providersToMigrate} />}

          {credentials && models && defaults && (
            <EasyModelSetup
              credentials={credentials}
              models={models}
              defaults={defaults}
              encryptionReady={encryptionReady}
            />
          )}

          {/* Default Model Selectors */}
          {models && defaults && (
            <DefaultModelSelectors models={models} defaults={defaults} />
          )}

          <ChatModelPolicyForm />

          {/* Provider Cards */}
          <div className="grid gap-4">
            {sortedProviders.map(provider => (
              <ProviderSection
                key={provider}
                provider={provider}
                credentials={credentialsByProvider[provider] || []}
                models={models || []}
                defaults={defaults || null}
                allCredentials={credentials || []}
                encryptionReady={encryptionReady}
              />
            ))}
          </div>

          {/* Help link */}
          <div className="border-t pt-4">
            <a
              href="https://github.com/lfnovo/open-notebook/blob/main/docs/5-CONFIGURATION/ai-providers.md"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-primary hover:underline"
            >
              {t('apiKeys.learnMore')}
            </a>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
