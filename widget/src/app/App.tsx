import type { ComponentChildren } from 'preact'
import { useEffect } from 'preact/hooks'

import type { Controller } from './controller'
import { ArticleView } from './components/ArticleView'
import { Header } from './components/Header'
import { HelpCenter } from './components/HelpCenter'
import { Home } from './components/Home'
import { IdentityGate } from './components/IdentityGate'
import { Thread } from './components/Thread'
import { useController } from './hooks'

export function App({ controller }: { controller: Controller }) {
  const state = useController(controller)

  useEffect(() => {
    void controller.boot()
  }, [controller])

  const accent = state.config.accent_color || '#5b46e5'
  const workspaceName = state.workspace?.name || 'Chat'
  const close = () => controller.requestClose()

  const screen = state.screen
  let body: ComponentChildren = null
  let header: ComponentChildren = null

  switch (screen.name) {
    case 'loading':
      body = <div class="sw-loading sw-loading-full">Loading…</div>
      break

    case 'error':
      body = (
        <div class="sw-gate">
          <div class="sw-gate-icon" aria-hidden="true">
            ⚠️
          </div>
          <p class="sw-gate-body">{screen.message}</p>
          <button type="button" class="sw-btn sw-btn-primary" onClick={() => void controller.boot()}>
            Try again
          </button>
        </div>
      )
      break

    case 'identity':
      header = <Header title={workspaceName} logoUrl={state.workspace?.logo_url} onClose={close} />
      body = <IdentityGate workspaceName={workspaceName} onRetry={() => void controller.boot()} />
      break

    case 'home':
      header = <Header title={workspaceName} logoUrl={state.workspace?.logo_url} onClose={close} />
      body = (
        <Home
          config={state.config}
          conversations={state.conversations}
          helpCenter={state.helpCenter}
          onOpenConversation={(id) => void controller.openConversation(id)}
          onNewConversation={() => controller.startNewConversation()}
          onOpenHelp={() => void controller.openHelp()}
        />
      )
      break

    case 'thread': {
      const active = screen.conversationId
        ? state.conversations.find((c) => c.id === screen.conversationId)
        : null
      header = (
        <Header
          title={workspaceName}
          subtitle="Conversation"
          onBack={() => controller.goHome()}
          onClose={close}
        />
      )
      body = (
        <Thread
          messages={state.messages}
          agentTyping={state.agentTyping}
          hasMore={state.nextCursor !== null}
          loading={state.loadingMessages}
          status={active?.status ?? null}
          csatDone={screen.conversationId ? Boolean(state.csatDone[screen.conversationId]) : false}
          greeting={state.config.greeting || 'Hi there 👋'}
          widgetKey={controller.widgetKey}
          pageControl={state.pageControl}
          pageTitle={state.page?.title || state.page?.path || ''}
          actionsAllowed={state.actionsAllowed}
          workingOnPage={state.workingOnPage}
          onAllowActions={(allowed) => void controller.setActionsAllowed(allowed)}
          onSend={(text) => void controller.send(text)}
          onTyping={(t) => controller.emitTyping(t)}
          onLoadMore={() => void controller.loadOlderMessages()}
          onCsat={(rating, feedback) =>
            screen.conversationId && void controller.submitCsat(screen.conversationId, rating, feedback)
          }
          onFeedback={(messageId, rating) => void controller.submitMessageFeedback(messageId, rating)}
          onRetry={(messageId) => void controller.retry(messageId)}
        />
      )
      break
    }

    case 'help':
      header = <Header title="Help" onBack={() => controller.goHome()} onClose={close} />
      body = (
        <HelpCenter
          articles={state.articles}
          query={state.articleQuery}
          onSearch={(q) => void controller.searchArticles(q)}
          onOpen={(slug) => void controller.openArticle(slug)}
        />
      )
      break

    case 'article':
      header = (
        <Header
          title={state.article?.collection?.name || 'Article'}
          onBack={() => controller.backToHelp()}
          onClose={close}
        />
      )
      body = <ArticleView article={state.article} loading={state.loadingArticle} />
      break
  }

  return (
    <div class="sw-app" style={`--sw-accent:${accent}`}>
      {header}
      <main class="sw-body">{body}</main>
    </div>
  )
}
