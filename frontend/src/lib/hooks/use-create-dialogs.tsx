'use client'

import { createContext, useContext, useState, useCallback, ReactNode } from 'react'
import dynamic from 'next/dynamic'

const AddSourceDialog = dynamic(
  () => import('@/components/sources/AddSourceDialog').then((module) => module.AddSourceDialog),
  { ssr: false },
)
const CreateNotebookDialog = dynamic(
  () => import('@/components/notebooks/CreateNotebookDialog').then((module) => module.CreateNotebookDialog),
  { ssr: false },
)
interface CreateDialogsContextType {
  openSourceDialog: () => void
  openNotebookDialog: () => void
}

const CreateDialogsContext = createContext<CreateDialogsContextType | null>(null)

export function CreateDialogsProvider({ children }: { children: ReactNode }) {
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false)
  const [notebookDialogOpen, setNotebookDialogOpen] = useState(false)

  const openSourceDialog = useCallback(() => setSourceDialogOpen(true), [])
  const openNotebookDialog = useCallback(() => setNotebookDialogOpen(true), [])

  return (
    <CreateDialogsContext.Provider
      value={{
        openSourceDialog,
        openNotebookDialog,
      }}
    >
      {children}
      {sourceDialogOpen && <AddSourceDialog open onOpenChange={setSourceDialogOpen} />}
      {notebookDialogOpen && <CreateNotebookDialog open onOpenChange={setNotebookDialogOpen} />}
    </CreateDialogsContext.Provider>
  )
}

export function useCreateDialogs() {
  const context = useContext(CreateDialogsContext)
  if (!context) {
    throw new Error('useCreateDialogs must be used within a CreateDialogsProvider')
  }
  return context
}
