import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useHasPerm } from '@/stores/auth'

import { RulesTab } from '../components/RulesTab'
import { WebhooksTab } from '../components/WebhooksTab'

export function Component() {
  const canReadRules = useHasPerm('automations:read')

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b px-6 py-4">
        <h1 className="text-lg font-semibold">Automation</h1>
        <p className="text-sm text-muted-foreground">
          Route, tag and reply automatically — and stream events to your own systems.
        </p>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-4xl">
          <Tabs defaultValue={canReadRules ? 'rules' : 'webhooks'}>
            <TabsList>
              <TabsTrigger value="rules">Rules</TabsTrigger>
              <TabsTrigger value="webhooks">Webhooks</TabsTrigger>
            </TabsList>
            <TabsContent value="rules" className="mt-4">
              {canReadRules ? (
                <RulesTab />
              ) : (
                <p className="rounded-md border p-6 text-center text-sm text-muted-foreground">
                  You don’t have access to automation rules.
                </p>
              )}
            </TabsContent>
            <TabsContent value="webhooks" className="mt-4">
              <WebhooksTab />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </div>
  )
}

export default Component
