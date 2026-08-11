import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

import {
  BANNER_ALIGNS,
  BANNER_DISMISS,
  BANNER_ICONS,
  BANNER_LAYOUTS,
  readableTextOn,
  type TourDraft,
} from '../lib'
import { t } from '@/i18n'

/** A colour input paired with its hex field; blank means "use the accent". */
function ColorField({
  id,
  label,
  value,
  fallback,
  hint,
  disabled,
  onChange,
}: {
  id: string
  label: string
  value: string
  fallback: string
  hint: string
  disabled: boolean
  onChange: (value: string) => void
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={`${id}-hex`}>{label}</Label>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="color"
          aria-label={`${label} swatch`}
          className="h-9 w-12 shrink-0 cursor-pointer rounded-md border bg-transparent"
          value={value || fallback}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        <Input
          id={`${id}-hex`}
          className="font-mono text-xs"
          placeholder={`Auto (${fallback})`}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
      <p className="text-[11px] text-muted-foreground">{hint}</p>
    </div>
  )
}

/** True-to-life miniature of the bar, so colour choices are judged in place. */
function BannerPreview({ draft }: { draft: TourDraft }) {
  const background = draft.bannerBackground.trim() || draft.accent
  const color = draft.bannerTextColor.trim() || readableTextOn(background)
  const boxed = !draft.bannerFullWidth

  return (
    <div className="grid gap-1.5">
      <span className="text-xs font-medium">{t('common.preview')}</span>
      <div
        className={cn(
          'flex flex-col gap-1 rounded-md border bg-muted/40 p-2',
          draft.bannerPosition === 'top' ? 'justify-start' : 'justify-end'
        )}
        data-testid="banner-preview"
      >
        {draft.bannerPosition === 'bottom' ? (
          <div className="h-6 rounded-sm bg-background/70" aria-hidden />
        ) : null}
        <div
          className={cn(
            'flex items-center gap-2 px-2.5 py-1.5 text-[11px]',
            boxed ? 'mx-auto w-4/5 rounded-md' : 'w-full',
            draft.bannerRounded && !boxed ? 'rounded-md' : '',
            draft.bannerAlign === 'center' ? 'justify-center text-center' : ''
          )}
          // Author-chosen colours: the one thing Tailwind cannot express.
          style={{ backgroundColor: background, color }}
        >
          {draft.bannerIcon ? <span aria-hidden>{draft.bannerIcon}</span> : null}
          <span className="truncate font-semibold">{draft.name || 'Your banner'}</span>
          <span
            className="ml-auto shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium"
            style={{ backgroundColor: color, color: background }}
          >
            {t('common.next')}
          </span>
        </div>
        {draft.bannerPosition === 'top' ? (
          <div className="h-6 rounded-sm bg-background/70" aria-hidden />
        ) : null}
      </div>
      <p className="text-[11px] text-muted-foreground">
        {draft.bannerLayout === 'inline'
          ? 'Pushes your page — nothing is covered.'
          : 'Floats over your page — may cover a fixed header.'}
      </p>
    </div>
  )
}

/**
 * Banner presentation.
 *
 * Shown for every tour, not just `kind: banner`: a flow can contain `banner`
 * steps, and these settings are what they render with. Colours default to
 * blank, meaning "follow the accent" — so a tour that never opens this panel
 * keeps looking exactly as it always has.
 */
export function BannerDesigner({
  draft,
  disabled,
  onChange,
}: {
  draft: TourDraft
  disabled: boolean
  onChange: (patch: Partial<TourDraft>) => void
}) {
  const background = draft.bannerBackground.trim() || draft.accent

  return (
    <div className="grid gap-3">
      <BannerPreview draft={draft} />

      <div className="grid gap-1.5">
        <Label htmlFor="banner-position">{t('tours.dock_it')}</Label>
        <NativeSelect
          id="banner-position"
          className="w-full"
          value={draft.bannerPosition}
          disabled={disabled}
          onChange={(e) => onChange({ bannerPosition: e.target.value as 'top' | 'bottom' })}
        >
          <NativeSelectOption value="top">{t('tours.top_of_the_page')}</NativeSelectOption>
          <NativeSelectOption value="bottom">{t('tours.bottom_of_the_page')}</NativeSelectOption>
        </NativeSelect>
      </div>

      <div className="grid gap-1.5">
        <Label htmlFor="banner-layout">{t('tours.layout')}</Label>
        <NativeSelect
          id="banner-layout"
          className="w-full"
          value={draft.bannerLayout}
          disabled={disabled}
          onChange={(e) =>
            onChange({ bannerLayout: e.target.value as TourDraft['bannerLayout'] })
          }
        >
          {BANNER_LAYOUTS.map((option) => (
            <NativeSelectOption key={option.value} value={option.value}>
              {option.label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <p className="text-[11px] text-muted-foreground">
          {BANNER_LAYOUTS.find((o) => o.value === draft.bannerLayout)?.hint}
        </p>
      </div>

      <div className="flex items-start justify-between gap-3">
        <div className="grid gap-0.5">
          <Label htmlFor="banner-full-width">{t('tours.full_width')}</Label>
          <p className="text-[11px] text-muted-foreground">{t('tours.off_to_centre_a_narrower_bar')}</p>
        </div>
        <Switch
          id="banner-full-width"
          checked={draft.bannerFullWidth}
          disabled={disabled}
          onCheckedChange={(bannerFullWidth) => onChange({ bannerFullWidth })}
        />
      </div>

      {draft.bannerFullWidth ? (
        <div className="flex items-start justify-between gap-3">
          <div className="grid gap-0.5">
            <Label htmlFor="banner-rounded">{t('tours.rounded_corners')}</Label>
            <p className="text-[11px] text-muted-foreground">{t('tours.softens_a_full_bleed_bar')}</p>
          </div>
          <Switch
            id="banner-rounded"
            checked={draft.bannerRounded}
            disabled={disabled}
            onCheckedChange={(bannerRounded) => onChange({ bannerRounded })}
          />
        </div>
      ) : (
        <div className="grid gap-1.5">
          <Label htmlFor="banner-max-width">Width (px)</Label>
          <Input
            id="banner-max-width"
            type="number"
            min={240}
            max={2000}
            step={20}
            placeholder="720"
            value={draft.bannerMaxWidth}
            disabled={disabled}
            onChange={(e) => onChange({ bannerMaxWidth: e.target.value })}
          />
          <p className="text-[11px] text-muted-foreground">{t('tours.anything_from_240_to_2000')}</p>
        </div>
      )}

      <div className="grid gap-1.5">
        <Label htmlFor="banner-align">{t('tours.align')}</Label>
        <NativeSelect
          id="banner-align"
          className="w-full"
          value={draft.bannerAlign}
          disabled={disabled}
          onChange={(e) => onChange({ bannerAlign: e.target.value as TourDraft['bannerAlign'] })}
        >
          {BANNER_ALIGNS.map((option) => (
            <NativeSelectOption key={option.value} value={option.value}>
              {option.label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
      </div>

      <ColorField
        id="banner-bg"
        label={t('tours.background')}
        value={draft.bannerBackground}
        fallback={draft.accent}
        hint="Blank follows the tour accent."
        disabled={disabled}
        onChange={(bannerBackground) => onChange({ bannerBackground })}
      />
      <ColorField
        id="banner-fg"
        label={t('tours.text_colour')}
        value={draft.bannerTextColor}
        fallback={readableTextOn(background)}
        hint="Blank picks whichever of black or white reads better."
        disabled={disabled}
        onChange={(bannerTextColor) => onChange({ bannerTextColor })}
      />

      <div className="grid gap-1.5">
        <Label htmlFor="banner-icon">{t('common.icon')}</Label>
        <div className="flex flex-wrap items-center gap-1">
          {BANNER_ICONS.map((icon) => (
            <button
              key={icon}
              type="button"
              aria-label={`Use ${icon} as the banner icon`}
              aria-pressed={draft.bannerIcon === icon}
              disabled={disabled}
              className={cn(
                'flex size-8 items-center justify-center rounded-md border text-base transition-colors hover:bg-accent',
                draft.bannerIcon === icon && 'border-primary bg-accent'
              )}
              onClick={() => onChange({ bannerIcon: draft.bannerIcon === icon ? '' : icon })}
            >
              {icon}
            </button>
          ))}
          <Input
            id="banner-icon"
            aria-label={t('tours.banner_icon')}
            className="w-20"
            maxLength={4}
            placeholder={t('common.none')}
            value={draft.bannerIcon}
            disabled={disabled}
            onChange={(e) => onChange({ bannerIcon: e.target.value })}
          />
        </div>
      </div>

      <div className="grid gap-1.5">
        <Label htmlFor="banner-dismiss">{t('tours.close_button')}</Label>
        <NativeSelect
          id="banner-dismiss"
          className="w-full"
          value={draft.bannerDismiss}
          disabled={disabled}
          onChange={(e) =>
            onChange({ bannerDismiss: e.target.value as TourDraft['bannerDismiss'] })
          }
        >
          {BANNER_DISMISS.map((option) => (
            <NativeSelectOption key={option.value} value={option.value}>
              {option.label}
            </NativeSelectOption>
          ))}
        </NativeSelect>
        <p className="text-[11px] text-muted-foreground">
          {BANNER_DISMISS.find((o) => o.value === draft.bannerDismiss)?.hint}
        </p>
      </div>
    </div>
  )
}
