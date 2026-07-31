import { useEffect, useState } from 'preact/hooks'

import type { AppState, Controller } from './controller'

/** Subscribe a component to the controller's state. */
export function useController(controller: Controller): AppState {
  const [state, setState] = useState<AppState>(controller.getState())
  useEffect(() => controller.subscribe(() => setState(controller.getState())), [controller])
  return state
}
