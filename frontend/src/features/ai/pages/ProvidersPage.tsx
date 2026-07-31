import { Cpu, Plus } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { useHasPerm } from '@/stores/auth'

import { AddProviderDialog } from '../components/AddProviderDialog'
import { ProviderCard } from '../components/ProviderCard'
import { AiNav, ErrorState, ListSkeleton, PageHeader, PageShell, ScrollBody } from '../components/shell'
import { useProviders } from '../hooks'

export function Component() {
  const canManage = useHasPerm('ai:manage')
  const [dialogOpen, setDialogOpen] = useState(false)
  const { data: providers, isLoading, isError, refetch } = useProviders()

  return (
    <PageShell>
      <PageHeader
        title="AI providers"
        description="Connect model providers and enable the models your agents can use"
        actions={
          canManage ? (
            <Button onClick={() => setDialogOpen(true)}>
              <Plus className="size-4" /> Add provider
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
              <EmptyTitle>No providers connected</EmptyTitle>
              <EmptyDescription>
                Add OpenAI, Anthropic, Google or a local model. A built-in mock provider is always
                available for testing.
              </EmptyDescription>
            </EmptyHeader>
            {canManage ? (
              <EmptyContent>
                <Button onClick={() => setDialogOpen(true)}>
                  <Plus className="size-4" /> Add provider
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
