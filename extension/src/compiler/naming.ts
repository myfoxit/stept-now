import type { Target } from '@stept/dom-capture';

/** Human names for steps and tours. Ported from the old
 * `packages/compiler/src/index.ts` (targetName / humanizeRole / cleanLabel /
 * brandName / slugify / deriveTitle) — the part of the compiler that decides
 * what a step is CALLED. Pure string logic; heavily unit-tested. */

/** Typographic quotes wrap element labels so a multi-word name reads as one
 * unit (Click on “Create new secret key”) without colliding with apostrophes. */
const LQ = '\u201c';
const RQ = '\u201d';
export const quote = (s: string): string => `${LQ}${s}${RQ}`;

/** Friendly noun for an element with no readable label, derived from its ARIA
 * role or DOM tag — so a step title is never a raw selector or the bare word
 * "element". */
function humanizeRole(target: Target): string {
  const role = target.aria?.role?.toLowerCase();
  const byRole: Record<string, string> = {
    button: 'button',
    link: 'link',
    checkbox: 'checkbox',
    radio: 'option',
    textbox: 'field',
    searchbox: 'search field',
    combobox: 'dropdown',
    listbox: 'dropdown',
    menuitem: 'menu item',
    menuitemcheckbox: 'menu item',
    tab: 'tab',
    switch: 'toggle',
    slider: 'slider',
    option: 'option',
    spinbutton: 'field',
  };
  if (role && byRole[role]) return byRole[role];
  const tag = target.fingerprint?.tagPath?.split('/').pop()?.toLowerCase();
  const byTag: Record<string, string> = {
    a: 'link',
    button: 'button',
    input: 'field',
    textarea: 'field',
    select: 'dropdown',
    img: 'image',
  };
  if (tag && byTag[tag]) return byTag[tag];
  return 'element';
}

/** Trim, de-noise and cap a candidate label; reject anything that is obviously
 * a CSS/xpath selector rather than something a human wrote. */
function cleanLabel(raw: string | undefined): string | null {
  if (!raw) return null;
  let s = raw.replace(/\s+/g, ' ').trim();
  s = s
    .replace(/^["'\u201c\u201d]+/, '')
    .replace(/["'\u201c\u201d\u2026\s:\u00b7|\u00bb]+$/g, '')
    .trim();
  if (!s) return null;
  if (s.startsWith('[') || s.startsWith('//')) return null; // selector, never a name
  if (s.length > 42) s = `${s.slice(0, 41).trimEnd()}\u2026`;
  return s;
}

/** Human name for the element a step acts on: a priority ladder of readable
 * signals, then a friendly role/tag fallback — never a raw selector. */
export function targetName(target: Target | null | undefined): string {
  if (!target) return 'element';
  const attrs = target.fingerprint?.attrs ?? {};
  const candidates = [
    target.aria?.name,
    target.text?.content,
    attrs['aria-label'],
    attrs['placeholder'],
    attrs['title'],
    attrs['name'],
    attrs['value'],
    attrs['alt'],
    target.fingerprint?.axName,
  ];
  for (const c of candidates) {
    const cleaned = cleanLabel(c);
    if (cleaned) return cleaned;
  }
  return humanizeRole(target);
}

// Known apps get a proper display name; everything else is humanized from host.
const BRANDS: Record<string, string> = {
  'github.com': 'GitHub',
  'gitlab.com': 'GitLab',
  'notion.so': 'Notion',
  'figma.com': 'Figma',
  'linear.app': 'Linear',
  'slack.com': 'Slack',
  'stripe.com': 'Stripe',
  'dashboard.stripe.com': 'Stripe',
  'hubspot.com': 'HubSpot',
  'shopify.com': 'Shopify',
  'airtable.com': 'Airtable',
  'zendesk.com': 'Zendesk',
  'intercom.com': 'Intercom',
  'salesforce.com': 'Salesforce',
  'localhost': 'Localhost',
};

function titleCaseToken(token: string): string {
  return token
    .split(/[-_]/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/** Pretty app/site name for a hostname: brand table first, else the
 * second-level domain Title-Cased ("app.acme-tools.io" → "Acme Tools"). */
export function brandName(host: string | undefined): string | undefined {
  if (!host) return undefined;
  const h = host.replace(/^www\./, '').toLowerCase();
  if (BRANDS[host]) return BRANDS[host];
  if (BRANDS[h]) return BRANDS[h];
  const parts = h.split('.');
  const apex = parts.slice(-2).join('.');
  if (BRANDS[apex]) return BRANDS[apex];
  const sld = parts.length >= 2 ? parts[parts.length - 2] : parts[0];
  return titleCaseToken(sld ?? h) || h;
}

/** Readable label for a URL: the brand, else host + path. */
export function humanUrl(url: string): string {
  try {
    const u = new URL(url);
    return brandName(u.hostname) ?? (u.pathname === '/' ? u.hostname : `${u.hostname}${u.pathname}`);
  } catch {
    return url;
  }
}

export function slugify(s: string): string {
  return (
    s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 48) || 'tour'
  );
}

/** Auto-title for the whole recording: "<Brand> — <first real step>". */
export function deriveTitle(
  stepTitles: readonly string[],
  domains: readonly string[],
): string {
  const brand = brandName(domains[0]);
  const first = stepTitles[0];
  if (brand && first) return `${brand} \u2014 ${first}`;
  if (brand) return `${brand} tour`;
  if (first) return first;
  return 'Recorded tour';
}

/** Short preview of a typed value for a step title. */
export function preview(value: string, cap = 24): string {
  const v = value.replace(/\s+/g, ' ').trim();
  return v.length > cap ? `${v.slice(0, cap)}\u2026` : v;
}
