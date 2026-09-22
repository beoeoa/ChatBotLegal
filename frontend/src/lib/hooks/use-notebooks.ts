import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { notebooksApi } from '@/lib/api/notebooks'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorKey } from '@/lib/utils/error-handler'
import { CreateNotebookRequest, NotebookResponse, UpdateNotebookRequest } from '@/lib/types/api'
import { useAuthStore } from '@/lib/stores/auth-store'

function shouldRetryNotebookRequest(failureCount: number, error: unknown) {
  const status = (error as { response?: { status?: number } })?.response?.status
  return ![401, 403, 404].includes(status ?? 0) && failureCount < 2
}

export function useNotebooks(archived?: boolean, enabled: boolean = true) {
  const userId = useAuthStore((state) => state.userId)
  return useQuery({
    queryKey: [...QUERY_KEYS.notebooks, { archived, owner: userId }],
    queryFn: () => notebooksApi.list({ archived, order_by: 'updated desc' }),
    enabled: enabled && !!userId,
    retry: shouldRetryNotebookRequest,
  })
}

export function useNotebook(id: string) {
  const userId = useAuthStore((state) => state.userId)
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: [...QUERY_KEYS.notebook(id), { owner: userId }],
    queryFn: () => notebooksApi.get(id),
    enabled: !!id && !!userId,
    retry: shouldRetryNotebookRequest,
    placeholderData: () => {
      const notebookLists = queryClient.getQueriesData<NotebookResponse[]>({
        queryKey: QUERY_KEYS.notebooks,
      })
      for (const [queryKey, notebooks] of notebookLists) {
        const ownerScope = Array.isArray(queryKey)
          ? queryKey.find((part) => typeof part === 'object' && part !== null && 'owner' in part) as { owner?: string } | undefined
          : undefined
        if (ownerScope?.owner !== userId) continue
        const match = notebooks?.find((notebook) => notebook.id === id)
        if (match) return match
      }
      return undefined
    },
  })
}

export function useCreateNotebook() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (data: CreateNotebookRequest) => notebooksApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.notebooks })
      toast({
        title: t('common.success'),
        description: t('notebooks.createSuccess'),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: t(getApiErrorKey(error, t('common.error'))),
        variant: 'destructive',
      })
    },
  })
}

export function useUpdateNotebook() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateNotebookRequest }) =>
      notebooksApi.update(id, data),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.notebooks })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.notebook(id) })
      toast({
        title: t('common.success'),
        description: t('notebooks.updateSuccess'),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: t(getApiErrorKey(error, t('common.error'))),
        variant: 'destructive',
      })
    },
  })
}

export function useNotebookDeletePreview(id: string, enabled: boolean = false) {
  return useQuery({
    queryKey: [...QUERY_KEYS.notebook(id), 'delete-preview'],
    queryFn: () => notebooksApi.deletePreview(id),
    enabled: !!id && enabled,
  })
}

export function useDeleteNotebook() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({
      id,
      deleteExclusiveSources = false,
    }: {
      id: string
      deleteExclusiveSources?: boolean
    }) => notebooksApi.delete(id, deleteExclusiveSources),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.notebooks })
      // Also invalidate sources since some may have been deleted
      queryClient.invalidateQueries({ queryKey: ['sources'] })
      toast({
        title: t('common.success'),
        description: t('notebooks.deleteSuccess'),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: t(getApiErrorKey(error, t('common.error'))),
        variant: 'destructive',
      })
    },
  })
}
