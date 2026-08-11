import { Cpu, Plus } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { useHasPerm } from '@/stores/auth'

import { AddProviderDialog } from '../components/AddProviderDialog'
import { ProviderCard } from '../components/ProviderCard'
import { AiNav, ErrorState, ListSkeleton, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { useProviders } from '../hooks'
import { t } from '@/i18n'

export function Component() {
  const canManage = useHasPerm('ai:manage')
  const [dialogOpen, setDialogOpen] = useState(false)
  const { data: providers, isLoading, isError, refetch } = useProviders()

  return (
    <PageShell>
      <PageHeader
        title={t('ai.ai_providers')}
        description={t('ai.connect_model_providers_and_enable_the')}
        actions={
          canManage ? (
            <Button onClick={() => setDialogOpen(true)}>
              <Plus className="size-4" /> {t('ai.add_provider')}
            </Button>
          ) : null
        }
      />
      <AiNav />
      <ScrollBody className="space-y-4">
        {isLoading ? (
          <ListSkeleton />
        ) : isError ? (
          <ErrorState onRetry={() => refetch()} />
        ) : !providers || providers.length === 0 ? (
          <Empty className="border">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Cpu />
              </EmptyMedia>
              <EmptyTitle>{t('ai.no_providers_connected')}</EmptyTitle>
              <EmptyDescription>
                {t('ai.add_openai_anthropic_google_or_a')}
              </EmptyDescription>
            </EmptyHeader>
            {canManage ? (
              <EmptyContent>
                <Button onClick={() => setDialogOpen(true)}>
                  <Plus className="size-4" /> {t('ai.add_provider')}
                </Button>
              </EmptyContent>
            ) : null}
          </Empty>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {providers.map((provider) => (
              <ProviderCard key={provider.id} provider={provider} />
            ))}
          </div>
        )}
      </ScrollBody>
      <AddProviderDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </PageShell>
  )
}

export default Component
