'use client'

import { useModalManager } from '@/lib/hooks/use-modal-manager'
import dynamic from 'next/dynamic'

const NoteEditorDialog = dynamic(
  () => import('@/app/(dashboard)/notebooks/components/NoteEditorDialog').then((module) => module.NoteEditorDialog),
  { ssr: false },
)
const SourceInsightDialog = dynamic(
  () => import('@/components/source/SourceInsightDialog').then((module) => module.SourceInsightDialog),
  { ssr: false },
)
const SourceDialog = dynamic(
  () => import('@/components/source/SourceDialog').then((module) => module.SourceDialog),
  { ssr: false },
)

/**
 * Modal Provider Component
 *
 * Renders modals based on URL query parameters (?modal=type&id=xxx)
 * Manages modal state through the useModalManager hook
 *
 * Supported modal types:
 * - source: Source detail modal
 * - note: Note editor modal
 * - insight: Source insight modal
 */
export function ModalProvider() {
  const { modalType, modalId, closeModal } = useModalManager()

  return (
    <>
      {/* Source Modal */}
      {modalType === 'source' && <SourceDialog
        open={modalType === 'source'}
        onOpenChange={(open) => {
          if (!open) closeModal()
        }}
        sourceId={modalId}
      />}

      {/* Note Modal */}
      {modalType === 'note' && <NoteEditorDialog
        open={modalType === 'note'}
        onOpenChange={(open) => {
          if (!open) closeModal()
        }}
        notebookId="" // Will need to be fetched or handled in Phase 9
        note={modalId ? { id: modalId, title: null, content: null } : undefined}
      />}

      {/* Source Insight Modal */}
      {modalType === 'insight' && <SourceInsightDialog
        open={modalType === 'insight'}
        onOpenChange={(open) => {
          if (!open) closeModal()
        }}
        insight={modalId ? { id: modalId, insight_type: '', content: '' } : undefined}
      />}
    </>
  )
}
