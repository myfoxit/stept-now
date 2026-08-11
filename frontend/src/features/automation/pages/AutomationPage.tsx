import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useHasPerm } from '@/stores/auth'

import { MacrosTab } from '../components/MacrosTab'
import { RulesTab } from '../components/RulesTab'
import { WebhooksTab } from '../components/WebhooksTab'
import { t } from '@/i18n'

export function Component() {
  const canReadRules = useHasPerm('automations:read')
  const canReadMacros = useHasPerm('conversations:read')

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <header className="border-b px-6 py-4">
        <h1 className="text-lg font-semibold">{t('automation.automation')}</h1>
        <p className="text-sm text-muted-foreground">
          {t('automation.route_tag_and_reply_automatically_and')}
        </p>
      </header>

      <div className="flex-1 overflow-auto p-6">
        <div className="mx-auto max-w-4xl">
          <Tabs defaultValue={canReadRules ? 'rules' : canReadMacros ? 'macros' : 'webhooks'}>
            <TabsList>
              <TabsTrigger value="rules">{t('automation.rules')}</TabsTrigger>
              <TabsTrigger value="macros">{t('automation.macros')}</TabsTrigger>
              <TabsTrigger value="webhooks">{t('automation.webhooks')}</TabsTrigger>
            </TabsList>
            <TabsContent value="rules" className="mt-4">
              {canReadRules ? (
                <RulesTab />
              ) : (
                <p className="rounded-md border p-6 text-center text-sm text-muted-foreground">
                  {t('automation.you_don_t_have_access_to')}
                </p>
              )}
            </TabsContent>
            <TabsContent value="macros" className="mt-4">
              {canReadMacros ? (
                <MacrosTab />
              ) : (
                <p className="rounded-md border p-6 text-center text-sm text-muted-foreground">
                  {t('automation.you_don_t_have_access_to_2')}
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
