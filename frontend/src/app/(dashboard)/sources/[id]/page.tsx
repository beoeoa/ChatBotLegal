'use client'

import { useRouter, useParams } from 'next/navigation'
import { useCallback } from 'react'
import { useSourceChat } from '@/lib/hooks/useSourceChat'
import { ChatPanel } from '@/components/source/ChatPanel'
import { useNavigation } from '@/lib/hooks/use-navigation'
import { SourceDetailContent } from '@/components/source/SourceDetailContent'
import { AppShell } from '@/components/layout/AppShell'

export default function SourceDetailPage() {
  const router = useRouter()
  const params = useParams()
  const sourceId = params?.id ? decodeURIComponent(params.id as string) : ''
  const navigation = useNavigation()

  // Initialize source chat
  const chat = useSourceChat(sourceId)

  const handleBack = useCallback(() => {
    const returnPath = navigation.getReturnPath()
    router.push(returnPath)
    navigation.clearReturnTo()
  }, [navigation, router])

  return (
    <AppShell>
      <div className="flex flex-col flex-1 h-[calc(100vh-4rem)] overflow-hidden">
        {/* Main content: Source detail + Chat */}
        <div className="flex-1 grid gap-6 lg:grid-cols-[2fr_1fr] overflow-hidden p-4 md:p-6">
          {/* Left column - Source detail */}
          <div className="overflow-y-auto px-4 pb-6">
            <SourceDetailContent
              sourceId={sourceId}
              showChatButton={false}
              onClose={handleBack}
            />
          </div>

          {/* Right column - Chat */}
          <div className="overflow-y-auto px-4 pb-6">
            <ChatPanel
              messages={chat.messages}
              isStreaming={chat.isStreaming}
              contextIndicators={chat.contextIndicators}
              onSendMessage={(message, model) => chat.sendMessage(message, model)}
              modelOverride={chat.currentSession?.model_override}
              onModelChange={(model) => {
                if (chat.currentSessionId) {
                  chat.updateSession(chat.currentSessionId, { model_override: model })
                }
              }}
              sessions={chat.sessions}
              currentSessionId={chat.currentSessionId}
              onCreateSession={(title) => chat.createSession({ title })}
              onSelectSession={chat.switchSession}
              onUpdateSession={(sessionId, title) => chat.updateSession(sessionId, { title })}
              onDeleteSession={chat.deleteSession}
              loadingSessions={chat.loadingSessions}
            />
          </div>
        </div>
      </div>
    </AppShell>
  )
}
