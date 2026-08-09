/**
 * @stept/react — React bindings for the Stept messenger + AI assistant.
 *
 * `<SteptProvider settings={…}>` boots the widget once on the client;
 * `useSteptAction(def)` teaches the assistant an action for as long as the
 * component that offers it is mounted. Everything proxies through @stept/js,
 * so calls are queued until the loader script arrives and are SSR-safe.
 */

import { createElement, Fragment, useEffect, type DependencyList, type ReactNode } from 'react'

import {
  loadStept,
  registerAction,
  removeAction,
  stept,
  type ClientActionDef,
  type SteptIdentity,
  type SteptSettings,
} from '@stept/js'

export type { ClientActionDef, SteptIdentity, SteptSettings }
export { stept }

export function SteptProvider({
  settings,
  children,
}: {
  settings: SteptSettings
  children?: ReactNode
}): ReactNode {
  // Re-boots when identity changes (login/logout); loadStept is idempotent on
  // the script itself.
  useEffect(() => {
    loadStept(settings)
  }, [settings.workspaceKey, settings.apiBase, settings.identity?.external_id])
  return createElement(Fragment, null, children)
}

/** The raw command function — `useStept()('open')`, `useStept()('startTour', id)`. */
export function useStept(): typeof stept {
  return stept
}

/**
 * Register a client action for the lifetime of the calling component.
 *
 * Re-registers (replace semantics) when `deps` change, so a handler that closes
 * over state stays fresh; removes the action on unmount, so the assistant is
 * never offered a verb this screen no longer has.
 */
export function useSteptAction(def: ClientActionDef, deps?: DependencyList): void {
  useEffect(() => {
    registerAction(def)
    return () => {
      removeAction(def.name)
    }
    // The def literal is fresh every render by nature; its identity for effect
    // purposes is (name + whatever the caller says the handler closes over).
  }, [def.name, ...(deps ?? [])])
}
