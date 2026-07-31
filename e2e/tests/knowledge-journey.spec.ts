import { expect, test } from '@playwright/test'

import { BACKEND, bearer, demoApiContext, getDemoWidgetKey, loginAsDemoOwner } from './helpers'

/**
 * Knowledge ingestion + authoring, end to end: a real PDF is uploaded through
 * the multi-file drop UI, parsed and indexed by the background ingest task, and
 * then retrieved from the search playground; and an article written in the
 * TipTap editor round-trips through markdown into the public help-center portal
 * and the widget's article search.
 */

/**
 * A minimal but structurally valid single-page PDF (correct xref offsets),
 * ported from `backend/tests/knowledge/test_parsers.py::build_pdf` so the test
 * needs no fixture file. `text` must avoid `(`, `)` and `\` — unescaped PDF
 * string delimiters.
 */
function buildPdf(text: string): Buffer {
  // Everything below is ASCII, so string offsets are byte offsets — which is
  // what the xref table has to record.
  const stream = `BT /F1 12 Tf 72 720 Td (${text}) Tj ET`
  const objects = [
    '1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n',
    '2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n',
    '3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] ' +
      '/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n',
    `4 0 obj << /Length ${stream.length} >> stream\n${stream}\nendstream endobj\n`,
    '5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n',
  ]

  let pdf = '%PDF-1.4\n'
  const offsets: number[] = []
  for (const object of objects) {
    offsets.push(pdf.length)
    pdf += object
  }
  const xrefPosition = pdf.length
  const size = objects.length + 1
  pdf += `xref\n0 ${size}\n0000000000 65535 f \n`
  for (const offset of offsets) pdf += `${String(offset).padStart(10, '0')} 00000 n \n`
  pdf += `trailer\n<< /Size ${size} /Root 1 0 R >>\nstartxref\n${xrefPosition}\n%%EOF`
  return Buffer.from(pdf, 'latin1')
}

test('a PDF uploaded through the drop UI is indexed and found in the search playground', async ({
  page,
}) => {
  // The parser titles a PDF from its filename ("-" → " "), so the row title and
  // the search-result title are both predictable — and unique per run.
  const stamp = Date.now()
  const filename = `zeppelin-ballast-manual-${stamp}.pdf`
  const documentTitle = `zeppelin ballast manual ${stamp}`
  const sourceName = `E2E uploads ${stamp}`
  const sentence = 'Zeppelin ballast must be recalibrated every 42 flight hours by a rigger.'

  await loginAsDemoOwner(page)
  await page.getByRole('link', { name: 'Knowledge', exact: true }).click()
  await page.waitForURL('**/knowledge')

  await page.getByRole('button', { name: /add source/i }).click()
  const dialog = page.getByRole('dialog', { name: 'Add a knowledge source' })
  await dialog.getByLabel('Source name').fill(sourceName)
  await dialog.getByLabel('Upload files').setInputFiles({
    name: filename,
    mimeType: 'application/pdf',
    buffer: buildPdf(sentence),
  })
  await expect(dialog.getByText(filename)).toBeVisible()
  await dialog.getByRole('button', { name: 'Create source' }).click()

  // Landing on the source page means the multipart batch upload was accepted.
  await page.waitForURL(/\/knowledge\/sources\/[0-9a-f-]+$/)
  const row = page.getByRole('row').filter({ hasText: documentTitle })
  await expect(row).toBeVisible({ timeout: 20_000 })
  // Ingestion runs in a background task; the documents query polls while the
  // document is pending/processing, so this is a UI-driven wait, not a sleep.
  await expect(row.getByText('Indexed')).toBeVisible({ timeout: 30_000 })

  // Retrieval finds the freshly indexed chunk. (The source page has no
  // knowledge tab bar of its own — go back to the section first.)
  await page.getByRole('link', { name: 'Back' }).click()
  await page.waitForURL('**/knowledge')
  await page.getByRole('link', { name: 'Search playground' }).click()
  await page.waitForURL('**/knowledge/search')
  await page.getByLabel('Search query').fill('zeppelin ballast')
  await page.getByRole('button', { name: 'Search', exact: true }).click()

  const hit = page.getByRole('listitem').filter({ hasText: documentTitle })
  await expect(hit).toBeVisible({ timeout: 20_000 })
  await expect(hit).toContainText('recalibrated every 42 flight hours')
})

test('an article written in the TipTap editor publishes to the public portal as markdown', async ({
  page,
  request,
}) => {
  const stamp = Date.now()
  const title = `Airship preflight ${stamp}`
  const heading = 'Preflight checklist'
  const boldWord = 'Always'

  const { token, workspaceId } = await demoApiContext(request)
  const me = await (
    await request.get(`${BACKEND}/api/v1/me`, { headers: bearer(token) })
  ).json()
  const workspaceSlug = me.memberships[0].workspace.slug as string

  await loginAsDemoOwner(page)
  await page.getByRole('link', { name: 'Knowledge', exact: true }).click()
  await page.getByRole('link', { name: 'Help center' }).click()
  await page.waitForURL('**/knowledge/articles')

  await page.getByRole('button', { name: 'New', exact: true }).click()
  await expect(page.getByText('Draft created')).toBeVisible()
  await page.getByLabel('Title').fill(title)

  // Compose with the rich-text toolbar — headings and marks, never raw markdown.
  const body = page.getByRole('textbox', { name: 'Article body' })
  await body.click()
  await page.getByRole('button', { name: 'Heading 2' }).click()
  await page.keyboard.type(heading)
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: 'Bold' }).click()
  await page.keyboard.type(boldWord)
  await page.getByRole('button', { name: 'Bold' }).click()
  await page.keyboard.type(' recalibrate the ballast before takeoff.')

  await expect(body.locator('h2')).toHaveText(heading)
  await expect(body.locator('strong')).toHaveText(boldWord)

  // The editor is controlled on markdown: the Markdown tab is the same state.
  await page.getByRole('tab', { name: 'Markdown' }).click()
  const markdown = page.getByRole('textbox', { name: 'Article markdown' })
  await expect(markdown).toHaveValue(new RegExp(`## ${heading}`))
  await expect(markdown).toHaveValue(new RegExp(`\\*\\*${boldWord}\\*\\*`))

  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(page.getByText('Article saved')).toBeVisible()
  await page.getByRole('button', { name: 'Publish', exact: true }).click()
  await expect(page.getByText('Article published')).toBeVisible()

  // --- the public portal, on the API origin, with no auth at all ------------
  const listed = await request.get(`${BACKEND}/api/v1/w/${workspaceId}/articles`, {
    headers: bearer(token),
  })
  const article = ((await listed.json()).items as Array<{ title: string; slug: string }>).find(
    (item) => item.title === title
  )
  expect(article, 'the published article is listed').toBeTruthy()

  const portal = await request.get(`${BACKEND}/portal/${workspaceSlug}/articles/${article!.slug}`)
  expect(portal.ok(), await portal.text()).toBeTruthy()
  const published = await portal.json()
  expect(published.title).toBe(title)
  expect(published.body).toContain(`## ${heading}`)
  expect(published.body).toContain(`**${boldWord}**`)
  expect(published.published_at).toBeTruthy()

  // ...and the widget help center retrieves it for a visitor. Publishing syncs
  // the article into the managed "articles" source, which re-indexes in a
  // background task — poll rather than assume it already ran.
  const boot = await request.post(`${BACKEND}/api/widget/boot`, {
    data: { widget_key: await getDemoWidgetKey(request) },
  })
  const widgetToken = (await boot.json()).token as string
  const deadline = Date.now() + 20_000
  let titles: string[] = []
  while (Date.now() < deadline) {
    const found = await request.get(
      `${BACKEND}/api/widget/articles?query=${encodeURIComponent(heading)}`,
      { headers: { 'X-Widget-Token': widgetToken } }
    )
    expect(found.ok(), await found.text()).toBeTruthy()
    const results = (await found.json()).results as Array<{ title: string }>
    titles = results.map((item) => item.title)
    if (titles.includes(title)) break
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  expect(titles, 'the widget help center surfaces the new article').toContain(title)
})
