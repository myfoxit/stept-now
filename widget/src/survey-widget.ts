/**
 * Host-DOM micro-survey (NPS / rating / text / select).
 *
 * One question at a time in a slideout card or a centred modal, with progress
 * dots and a markdown thanks message. Dismissing mid-flow submits what has been
 * answered so far with `completed:false` — a partial NPS is still a signal, and
 * the results page counts only completed responses for its rate.
 *
 * Frequency mirrors the tour player: the backend decides eligibility, and the
 * widget additionally keeps a local seen-set for anonymous visitors — which
 * `every_time` surveys deliberately bypass.
 */

import { renderMarkdown } from './app/md'
import type { Survey, SurveyAnswer, SurveyQuestion } from './types'

// --- pure helpers (unit-tested) ---------------------------------------------

/** localStorage key holding the surveys this visitor already answered/skipped. */
export function surveySeenKey(widgetKey: string): string {
  return `stept:surveys-seen:${widgetKey}`
}

/** First survey not already seen locally; `every_time` bypasses the seen-set. */
export function selectFirstEligibleSurvey(
  surveys: readonly Survey[],
  seen: Iterable<string> = [],
): Survey | null {
  const seenSet = seen instanceof Set ? seen : new Set(seen)
  for (const survey of surveys) {
    if (!survey.questions?.length) continue
    if (survey.frequency_type === 'every_time' || !seenSet.has(survey.id)) return survey
  }
  return null
}

/** Wire shape for the answers collected so far, in question order. */
export function collectAnswers(
  questions: readonly SurveyQuestion[],
  values: ReadonlyMap<string, number | string>,
): SurveyAnswer[] {
  const out: SurveyAnswer[] = []
  for (const question of questions) {
    const value = values.get(question.id)
    if (value === undefined || value === '') continue
    out.push({ question_id: question.id, value })
  }
  return out
}

/** Can the flow move past this question? (required ⇒ needs a value) */
export function canAdvance(
  question: SurveyQuestion,
  values: ReadonlyMap<string, number | string>,
): boolean {
  if (!question.required) return true
  const value = values.get(question.id)
  return value !== undefined && value !== ''
}

// --- the DOM widget ---------------------------------------------------------

export interface SurveyWidgetOptions {
  widgetKey: string
  doc?: Document
  win?: Window & typeof globalThis
  /** POST the answers; `completed:false` is a partial (dismissed) submission. */
  onSubmit?: (surveyId: string, answers: SurveyAnswer[], completed: boolean) => void
  /** Fired once the card leaves the screen (loader clears its overlay lock). */
  onClose?: (surveyId: string, completed: boolean) => void
}

const STYLE_ID = 'stept-survey-style'
const CSS = `
.stept-sv-veil{position:fixed;inset:0;z-index:2147482940;background:rgba(15,23,42,.5)}
.stept-sv-veil[hidden]{display:none}
.stept-sv-card{position:fixed;z-index:2147482941;box-sizing:border-box;width:340px;max-width:calc(100vw - 32px);
  border-radius:16px;background:#fff;color:#0f172a;box-shadow:0 16px 48px rgba(15,23,42,.28);padding:16px;
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.stept-sv-card[hidden]{display:none}
.stept-sv-card.stept-sv-slideout{right:20px;bottom:92px}
.stept-sv-card.stept-sv-modal{top:50%;left:50%;transform:translate(-50%,-50%);width:400px}
.stept-sv-card:focus{outline:2px solid var(--stept-accent,#5b46e5);outline-offset:2px}
.stept-sv-close{position:absolute;top:8px;right:8px;border:0;background:transparent;color:#94a3b8;
  font-size:16px;line-height:1;cursor:pointer;padding:4px}
.stept-sv-q{margin:0 0 12px;font-size:15px;font-weight:600;padding-right:16px}
.stept-sv-nps{display:flex;flex-wrap:wrap;gap:4px;margin:0 0 8px}
.stept-sv-nps button{flex:1 0 auto;min-width:26px;border:1px solid rgba(100,116,139,.35);background:transparent;
  border-radius:6px;padding:6px 0;font:600 12px/1 inherit;color:inherit;cursor:pointer}
.stept-sv-nps button[aria-pressed="true"]{background:var(--stept-accent,#5b46e5);color:#fff;
  border-color:var(--stept-accent,#5b46e5)}
.stept-sv-scale{display:flex;justify-content:space-between;font-size:11px;color:#64748b;margin:0 0 12px}
.stept-sv-stars{display:flex;gap:6px;margin:0 0 12px}
.stept-sv-stars button{border:0;background:transparent;font-size:26px;line-height:1;cursor:pointer;padding:0;
  color:rgba(100,116,139,.45)}
.stept-sv-stars button[aria-pressed="true"]{color:var(--stept-accent,#5b46e5)}
.stept-sv-options{display:flex;flex-direction:column;gap:6px;margin:0 0 12px}
.stept-sv-options button{border:1px solid rgba(100,116,139,.35);background:transparent;border-radius:8px;
  padding:8px 10px;text-align:left;font:inherit;color:inherit;cursor:pointer}
.stept-sv-options button[aria-pressed="true"]{border-color:var(--stept-accent,#5b46e5);
  background:rgba(99,102,241,.12)}
.stept-sv-text{width:100%;box-sizing:border-box;min-height:72px;border-radius:8px;padding:8px;font:inherit;
  border:1px solid rgba(100,116,139,.35);background:transparent;color:inherit;margin:0 0 12px;resize:vertical}
.stept-sv-foot{display:flex;align-items:center;justify-content:space-between;gap:8px}
.stept-sv-dots{display:flex;gap:5px}
.stept-sv-dots i{width:6px;height:6px;border-radius:50%;background:rgba(100,116,139,.35)}
.stept-sv-dots i.on{background:var(--stept-accent,#5b46e5)}
.stept-sv-btn{border:0;border-radius:8px;padding:7px 14px;font:600 13px/1.2 inherit;cursor:pointer;
  background:var(--stept-accent,#5b46e5);color:#fff}
.stept-sv-btn[disabled]{opacity:.5;cursor:default}
.stept-sv-btn.ghost{background:transparent;color:#64748b}
.stept-sv-thanks p{margin:0}
@media (prefers-color-scheme:dark){
  .stept-sv-card{background:#1e293b;color:#f1f5f9}
  .stept-sv-scale{color:#94a3b8}
}
@media (max-width:480px){
  .stept-sv-card.stept-sv-slideout{left:16px;right:16px;width:auto}
}
`

export class SurveyWidget {
  private doc: Document
  private win: Window & typeof globalThis
  private opts: SurveyWidgetOptions

  private survey: Survey | null = null
  private index = 0
  private values = new Map<string, number | string>()
  private submitted = false
  private veil: HTMLElement | null = null
  private card: HTMLElement | null = null
  private closeTimer: number | null = null
  private onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' && this.survey) {
      event.preventDefault()
      this.dismiss()
    }
  }

  constructor(opts: SurveyWidgetOptions) {
    this.opts = opts
    this.doc = opts.doc ?? document
    this.win = opts.win ?? (this.doc.defaultView as Window & typeof globalThis) ?? window
  }

  get active(): boolean {
    return this.survey !== null
  }

  get surveyId(): string | null {
    return this.survey?.id ?? null
  }

  /** Answers collected so far (test/debug surface). */
  get answers(): SurveyAnswer[] {
    return this.survey ? collectAnswers(this.survey.questions, this.values) : []
  }

  mount(survey: Survey): void {
    if (this.survey) this.close(false)
    if (!survey.questions?.length) return
    this.survey = survey
    this.index = 0
    this.values.clear()
    this.submitted = false
    this.ensureStyle()
    this.build(survey)
    this.render()
    this.doc.addEventListener('keydown', this.onKeyDown, true)
  }

  /** Answer the current question (auto-advances for single-tap kinds). */
  setValue(questionId: string, value: number | string, advance = false): void {
    this.values.set(questionId, value)
    if (advance) this.next()
    else this.render()
  }

  next(): void {
    const survey = this.survey
    if (!survey) return
    const question = survey.questions[this.index]
    if (question && !canAdvance(question, this.values)) return
    if (this.index < survey.questions.length - 1) {
      this.index += 1
      this.render()
      return
    }
    this.submit(true)
  }

  /** Close mid-flow: partial answers are still worth reporting. */
  dismiss(): void {
    const survey = this.survey
    if (!survey) return
    if (!this.submitted && this.answers.length > 0) {
      this.submitted = true
      this.opts.onSubmit?.(survey.id, this.answers, false)
    }
    this.close(false)
  }

  close(completed: boolean): void {
    const survey = this.survey
    this.clearCloseTimer()
    this.doc.removeEventListener('keydown', this.onKeyDown, true)
    this.veil?.remove()
    this.card?.remove()
    this.veil = this.card = null
    this.survey = null
    if (survey) this.opts.onClose?.(survey.id, completed)
  }

  // --- flow ----------------------------------------------------------------

  private submit(completed: boolean): void {
    const survey = this.survey
    if (!survey) return
    if (!this.submitted) {
      this.submitted = true
      this.opts.onSubmit?.(survey.id, this.answers, completed)
    }
    this.renderThanks(survey)
    this.closeTimer = this.win.setTimeout(() => this.close(true), 3200)
  }

  // --- DOM -----------------------------------------------------------------

  private ensureStyle(): void {
    if (this.doc.getElementById(STYLE_ID)) return
    const style = this.doc.createElement('style')
    style.id = STYLE_ID
    style.textContent = CSS
    this.doc.head.appendChild(style)
  }

  private build(survey: Survey): void {
    const modal = survey.presentation === 'modal'
    const accent = survey.theme?.accent || '#5b46e5'
    if (modal) {
      const veil = node(this.doc, 'div', 'stept-sv-veil')
      veil.onclick = () => this.dismiss()
      this.doc.body.appendChild(veil)
      this.veil = veil
    }
    const card = node(this.doc, 'div', `stept-sv-card ${modal ? 'stept-sv-modal' : 'stept-sv-slideout'}`)
    card.style.setProperty('--stept-accent', accent)
    card.setAttribute('role', 'dialog')
    card.setAttribute('aria-modal', modal ? 'true' : 'false')
    card.setAttribute('aria-label', survey.name)
    card.setAttribute('tabindex', '-1')
    this.doc.body.appendChild(card)
    this.card = card
    if (typeof card.focus === 'function') {
      try {
        card.focus({ preventScroll: true })
      } catch {
        card.focus()
      }
    }
  }

  private render(): void {
    const survey = this.survey
    const card = this.card
    if (!survey || !card) return
    const question = survey.questions[this.index]
    if (!question) return
    card.innerHTML = ''

    const close = node(this.doc, 'button', 'stept-sv-close')
    close.textContent = '×'
    close.setAttribute('aria-label', 'Dismiss survey')
    close.onclick = () => this.dismiss()
    card.appendChild(close)

    const heading = node(this.doc, 'p', 'stept-sv-q')
    heading.id = `stept-sv-q-${question.id}`
    heading.textContent = question.question
    card.appendChild(heading)
    // The dialog keeps its `aria-label` (the survey name) as its accessible
    // name: pointing `aria-labelledby` at this heading would override it, so the
    // survey would be nameless to assistive tech and the dialog would appear to
    // rename itself on every question. The heading is the question's label
    // instead — each input references it via its own `aria-labelledby`.
    card.setAttribute('aria-describedby', heading.id)

    switch (question.type) {
      case 'nps':
        card.appendChild(this.renderNps(question))
        card.appendChild(this.renderScaleLabels())
        break
      case 'rating':
        card.appendChild(this.renderStars(question))
        break
      case 'select':
        card.appendChild(this.renderOptions(question))
        break
      default:
        card.appendChild(this.renderText(question))
    }
    card.appendChild(this.renderFoot(survey, question))
  }

  private renderNps(question: SurveyQuestion): HTMLElement {
    const wrap = node(this.doc, 'div', 'stept-sv-nps')
    for (let score = 0; score <= 10; score++) {
      const btn = node(this.doc, 'button')
      btn.textContent = String(score)
      btn.setAttribute('aria-pressed', this.values.get(question.id) === score ? 'true' : 'false')
      btn.setAttribute('aria-label', `${score}`)
      btn.onclick = () => this.setValue(question.id, score, true)
      wrap.appendChild(btn)
    }
    return wrap
  }

  private renderScaleLabels(): HTMLElement {
    const scale = node(this.doc, 'div', 'stept-sv-scale')
    const low = node(this.doc, 'span')
    low.textContent = 'Not likely'
    const high = node(this.doc, 'span')
    high.textContent = 'Very likely'
    scale.appendChild(low)
    scale.appendChild(high)
    return scale
  }

  private renderStars(question: SurveyQuestion): HTMLElement {
    const wrap = node(this.doc, 'div', 'stept-sv-stars')
    const current = Number(this.values.get(question.id) ?? 0)
    for (let star = 1; star <= 5; star++) {
      const btn = node(this.doc, 'button')
      btn.textContent = star <= current ? '★' : '☆'
      btn.setAttribute('aria-pressed', current === star ? 'true' : 'false')
      btn.setAttribute('aria-label', `${star} star${star === 1 ? '' : 's'}`)
      btn.onclick = () => this.setValue(question.id, star, true)
      wrap.appendChild(btn)
    }
    return wrap
  }

  private renderOptions(question: SurveyQuestion): HTMLElement {
    const wrap = node(this.doc, 'div', 'stept-sv-options')
    for (const option of question.options ?? []) {
      const btn = node(this.doc, 'button')
      btn.textContent = option
      btn.setAttribute('aria-pressed', this.values.get(question.id) === option ? 'true' : 'false')
      btn.onclick = () => this.setValue(question.id, option, true)
      wrap.appendChild(btn)
    }
    return wrap
  }

  private renderText(question: SurveyQuestion): HTMLElement {
    const area = this.doc.createElement('textarea')
    area.className = 'stept-sv-text'
    area.value = String(this.values.get(question.id) ?? '')
    area.setAttribute('aria-labelledby', `stept-sv-q-${question.id}`)
    area.oninput = () => {
      this.values.set(question.id, area.value)
      const submit = this.card?.querySelector('.stept-sv-btn:not(.ghost)') as HTMLButtonElement | null
      if (submit) submit.disabled = !canAdvance(question, this.values)
    }
    return area
  }

  private renderFoot(survey: Survey, question: SurveyQuestion): HTMLElement {
    const foot = node(this.doc, 'div', 'stept-sv-foot')
    const dots = node(this.doc, 'div', 'stept-sv-dots')
    survey.questions.forEach((_q, i) => {
      const dot = node(this.doc, 'i', i <= this.index ? 'on' : '')
      dots.appendChild(dot)
    })
    foot.appendChild(dots)

    const actions = node(this.doc, 'div', 'stept-sv-actions')
    const isLast = this.index === survey.questions.length - 1
    if (!question.required) {
      const skip = node(this.doc, 'button', 'stept-sv-btn ghost')
      skip.textContent = 'Skip'
      skip.onclick = () => this.next()
      actions.appendChild(skip)
    }
    const nextBtn = this.doc.createElement('button')
    nextBtn.className = 'stept-sv-btn'
    nextBtn.textContent = isLast ? 'Submit' : 'Next'
    nextBtn.disabled = !canAdvance(question, this.values)
    nextBtn.onclick = () => this.next()
    actions.appendChild(nextBtn)
    foot.appendChild(actions)
    return foot
  }

  private renderThanks(survey: Survey): void {
    const card = this.card
    if (!card) return
    card.innerHTML = ''
    const thanks = node(this.doc, 'div', 'stept-sv-thanks')
    thanks.innerHTML = renderMarkdown(survey.thanks_message || 'Thanks for the feedback!')
    card.appendChild(thanks)
  }

  private clearCloseTimer(): void {
    if (this.closeTimer !== null) {
      this.win.clearTimeout(this.closeTimer)
      this.closeTimer = null
    }
  }
}

function node(doc: Document, tag: string, className = ''): HTMLElement {
  const el = doc.createElement(tag)
  if (className) el.className = className
  return el
}
