import { isRouteErrorResponse, Link, useRouteError } from 'react-router'

import { Button } from '@/components/ui/button'
import { t } from '@/i18n'

export function RouteErrorPage() {
  const error = useRouteError()
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : 'Something went wrong'

  return (
    <div className="flex h-svh flex-col items-center justify-center gap-4 p-8 text-center">
      <h1 className="text-2xl font-semibold">{t('common.well_that_broke')}</h1>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      <div className="flex gap-2">
        <Button onClick={() => window.location.reload()}>{t('common.reload')}</Button>
        <Button variant="outline" asChild>
          <Link to="/">{t('common.back_home')}</Link>
        </Button>
      </div>
    </div>
  )
}
