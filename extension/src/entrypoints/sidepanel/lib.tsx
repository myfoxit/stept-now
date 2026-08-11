import { useEffect, useState } from 'react';
import {
  CheckSquare,
  Hand,
  Keyboard,
  ListChecks,
  MousePointerClick,
  Navigation,
  PanelTop,
  Square,
  Timer,
  Type as TypeIcon,
  Upload,
} from 'lucide-react';
import type { PanelToBg } from '../../messages';
import type { TourStepType } from '../../types';

/** Shared side-panel plumbing: the background message bridge, brand mark, step
 * iconography and small formatting helpers — one home so every panel view
 * renders the same product language. Ported from the old repo's `lib.tsx`. */

export function sendBg<T = unknown>(msg: PanelToBg): Promise<T> {
  return chrome.runtime.sendMessage(msg) as Promise<T>;
}

/** One lucide glyph per step type — mirrors the dashboard's step cards so the
 * recorder and the tour editor read as the same product. */
export const STEP_ICONS: Record<TourStepType | string, typeof MousePointerClick> = {
  tooltip: MousePointerClick,
  hotspot: Hand,
  modal: PanelTop,
  banner: PanelTop,
  action: TypeIcon,
  wait: Timer,
  navigate: Navigation,
  select: ListChecks,
  check: CheckSquare,
  upload: Upload,
  key: Keyboard,
  stop: Square,
};

/** The Stept mark — two bars, the leading one stepped right and down. The
 *  geometry lives in `assets/brand/`; this reproduces it, it does not redraw it.
 *
 *  `mono` (default) inherits `currentColor` — that is the panel header, where
 *  the mark is chrome standing beside a word. `split` paints the leading bar
 *  with `--accent`, which is already the brand indigo and already lifts in the
 *  dark scheme; it is for the sign-in screen, the panel's front door.
 *
 *  Below 20px it swaps to the optically corrected small geometry — thicker
 *  bars, wider gap — because the 24px master closes up at that size. */
export function Logo({
  size = 22,
  variant = 'mono',
}: {
  size?: number;
  variant?: 'mono' | 'split';
}) {
  const small = size < 20;
  const lead = variant === 'split' ? 'var(--accent)' : 'currentColor';
  return (
    <svg
      width={size}
      height={size}
      viewBox={small ? '0 0 32 32' : '0 0 24 24'}
      aria-hidden="true"
    >
      {small ? (
        <>
          <rect x="4" y="5" width="20" height="9" rx="3.8" fill="currentColor" />
          <rect x="8" y="18" width="20" height="9" rx="3.8" fill={lead} />
        </>
      ) : (
        <>
          <rect x="3" y="3.5" width="15" height="7" rx="2.9" fill="currentColor" />
          <rect x="6" y="13.5" width="15" height="7" rx="2.9" fill={lead} />
        </>
      )}
    </svg>
  );
}

/** Re-render once a second while active, so the elapsed timer ticks. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

export function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** Relative time for the tour list ("2h ago") — coarse on purpose. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return '';
  const s = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Copy to clipboard with a transient "Copied" state for the caller's button. */
export function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  return [
    copied,
    (text: string) => {
      void navigator.clipboard?.writeText(text).then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1400);
      });
    },
  ];
}
