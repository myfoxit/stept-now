import { beforeEach, describe, expect, it } from 'vitest';
import { buildTarget, framePathOf, shadowPathOf } from './capture';
import { computeFingerprint } from './fingerprint';
import { generateSelectors, isStableId, isTestIdSelector, looksLikeVolatileText, queryBySelector, resolveSelector } from './selectors';
import { resolveTarget, scoreCandidate, targetFragility } from './resolve';
import type { Target } from './types';
import { interactiveAncestor, preferLaidOut } from './util';

const PAGE = `
  <main>
    <section aria-label="Documents">
      <h2>Your documents</h2>
      <button id="new-doc" class="btn btn-primary css-1a2b3c4d" data-testid="create-doc">New document</button>
      <ul>
        <li><a href="/d/1">Quarterly report</a></li>
        <li><a href="/d/2">Meeting notes</a></li>
      </ul>
    </section>
    <form>
      <label for="title">Document title</label>
      <input id="title" name="title" type="text" placeholder="Untitled" />
      <select name="folder">
        <option value="a">Folder A</option>
        <option value="b">Folder B</option>
      </select>
      <button type="submit">Save</button>
    </form>
  </main>`;

beforeEach(() => {
  document.body.innerHTML = PAGE;
});

describe('selector generation', () => {
  it('ranks testid > id > aria > css and marks uniqueness', () => {
    const btn = document.getElementById('new-doc')!;
    const sels = generateSelectors(btn);
    expect(sels[0]).toMatchObject({ kind: 'css', value: '[data-testid="create-doc"]', uniqueAtRecord: true, score: 0.95 });
    expect(isTestIdSelector(sels[0]!.value)).toBe(true);
    const kinds = sels.map((s) => s.kind);
    expect(kinds).toContain('aria');
    expect(kinds).toContain('xpath');
    // scores must be sorted descending
    expect([...sels].sort((a, b) => b.score - a.score)).toEqual(sels);
    // dynamic css-in-js class must not appear in generated css selectors
    for (const s of sels) expect(s.value).not.toContain('css-1a2b3c4d');
  });

  it('queries aria and text selectors (prefix optional)', () => {
    const byAria = queryBySelector(document, { kind: 'aria', value: 'aria/New document[role="button"]' });
    expect(byAria).toHaveLength(1);
    expect(byAria[0]!.id).toBe('new-doc');
    const byText = queryBySelector(document, { kind: 'text', value: 'text/Meeting notes' });
    expect(byText).toHaveLength(1);
    expect(byText[0]!.getAttribute('href')).toBe('/d/2');
    // resolveSelector accepts the bare value too
    expect(resolveSelector('text', 'Meeting notes', document)).toHaveLength(1);
    expect(resolveSelector('css', '#new-doc', document)).toHaveLength(1);
    expect(resolveSelector('css', '((not a selector', document)).toEqual([]);
  });

  it('resolves an xpath selector', () => {
    const hits = resolveSelector('xpath', `xpath/${generateSelectors(document.getElementById('title')!)
      .find((s) => s.kind === 'xpath')!
      .value.replace(/^xpath\//, '')}`, document);
    expect(hits[0]).toBe(document.getElementById('title'));
  });

  it('demotes a value-specific selector when the name is volatile data (first-result case)', () => {
    // A "143 comments" link identical in role to every other result's link: its
    // accessible name is instance data, so replay must key off position, not the
    // count. The top-ranked selector must be structural, not aria/text.
    document.body.innerHTML = `<main><ol><li class="story"><a href="/item?id=1">143 comments</a></li></ol></main>`;
    const link = document.querySelector('a')!;
    const sels = generateSelectors(link);
    expect(sels[0]!.kind === 'aria' || sels[0]!.kind === 'text').toBe(false);
    const aria = sels.find((s) => s.kind === 'aria');
    expect(aria).toBeTruthy();
    expect(aria!.score).toBeLessThan(sels[0]!.score);
    // a stable label is NOT demoted
    document.body.innerHTML = `<main><button>Save document</button></main>`;
    const btn = document.querySelector('button')!;
    const stable = generateSelectors(btn).find((s) => s.kind === 'aria');
    expect(stable?.score).toBe(0.85);
  });

  it('rejects framework-generated ids (React useId, MUI, emotion) as selectors', () => {
    document.body.innerHTML = `<div>
      <button id="_r_1lf_">a</button>
      <button id="email-:r7:">b</button>
      <button id="mui-42">c</button>
      <button id="headlessui-menu-button-:r5:">d</button>
      <button id="save-btn">e</button>
    </div>`;
    const idSelector = (sel: string) =>
      generateSelectors(document.querySelector(sel)!).some((s) => s.kind === 'css' && s.value.startsWith('#'));
    expect(idSelector('#_r_1lf_')).toBe(false);
    expect(idSelector('[id="email-:r7:"]')).toBe(false);
    expect(idSelector('#mui-42')).toBe(false);
    expect(idSelector('#save-btn')).toBe(true); // a real, stable id still used
    // and the blacklist itself
    expect(isStableId('save-btn')).toBe(true);
    expect(isStableId('radix-:r3:')).toBe(false);
    expect(isStableId('ng-tns-c12-4')).toBe(false);
    expect(isStableId('7f3a9b2c4d5e')).toBe(false);
  });
});

describe('volatile-text detection', () => {
  it('flags counts, timestamps, prices and dates — not ordinary labels', () => {
    for (const v of ['143 comments', '4.3k points', '2 years ago', '$1,299.00', '48,000 credits', 'Oct 8, 2018', '12%', '14:30', '2024-12-25', '907'])
      expect(looksLikeVolatileText(v), v).toBe(true);
    for (const v of ['Save', 'New document', 'Web3', 'COVID-19', 'H2', 'Add to cart', 'Sign in', '3D model'])
      expect(looksLikeVolatileText(v), v).toBe(false);
  });
});

describe('fingerprints', () => {
  it('is stable across dynamic class churn', () => {
    const btn = document.getElementById('new-doc')!;
    const before = computeFingerprint(btn);
    btn.className = 'btn btn-primary css-9z8y7x6w hover-active';
    const after = computeFingerprint(btn);
    expect(after.stableHash).toBe(before.stableHash);
  });

  it('is independent of attribute ORDER in the markup', () => {
    document.body.innerHTML = `<main><button id="b1" data-testid="t" aria-label="Save" type="button" class="ok">S</button></main>`;
    const a = computeFingerprint(document.getElementById('b1')!);
    document.body.innerHTML = `<main><button class="ok" type="button" aria-label="Save" data-testid="t" id="b1">S</button></main>`;
    const b = computeFingerprint(document.getElementById('b1')!);
    expect(b.elementHash).toBe(a.elementHash);
    expect(b.stableHash).toBe(a.stableHash);
    expect(b.structuralHash).toBe(a.structuralHash);
  });

  it('separates the three tiers (exact / content-drift / structure-only)', () => {
    const btn = document.getElementById('new-doc')!;
    const fp = computeFingerprint(btn);
    expect(fp.structuralHash).toBeTruthy();
    expect(fp.structuralHash).not.toBe(fp.elementHash);
    expect(fp.tagPath.endsWith('button')).toBe(true);
    expect(fp.attrs['data-testid']).toBe('create-doc');

    // an element carrying a CONTENT attribute (placeholder) hashes differently
    // per tier: elementHash sees it, stableHash/structuralHash do not
    const input = document.getElementById('title')!;
    const before = computeFingerprint(input);
    expect(before.elementHash).not.toBe(before.stableHash);
    input.setAttribute('placeholder', 'Give it a name'); // content drift
    const after = computeFingerprint(input);
    expect(after.elementHash).not.toBe(before.elementHash);
    expect(after.stableHash).toBe(before.stableHash);
    expect(after.structuralHash).toBe(before.structuralHash);
    input.id = 'title-v2'; // id churn on top of it
    expect(computeFingerprint(input).structuralHash).toBe(before.structuralHash);
  });

  it('captures label-derived accessible names for inputs', () => {
    const input = document.getElementById('title')!;
    const fp = computeFingerprint(input);
    expect(fp.axName).toBe('Document title');
    expect(fp.neighborText.length).toBeGreaterThan(0);
  });
});

describe('target capture', () => {
  it('captures text, aria, hints, fingerprint and an empty frame path in the top document', () => {
    const btn = document.getElementById('new-doc')!;
    const t = buildTarget(btn);
    expect(t.text?.content).toBe('New document');
    expect(t.aria).toEqual({ role: 'button', name: 'New document' });
    expect(t.hints?.container).toBe('Documents');
    expect(t.fingerprint?.tagPath).toContain('button');
    expect(t.frame).toEqual([]);
    expect(t.shadowPath).toBeUndefined();
    expect(t.selectors.length).toBeGreaterThan(2);
  });

  it('climbs to the enclosing control instead of the inner glyph (opt-out via climb:false)', () => {
    document.body.innerHTML = `<main><button id="icon-btn" aria-label="Delete"><svg><path id="glyph" d="M0 0"></path></svg></button></main>`;
    const glyph = document.querySelector('#glyph')!;
    expect(interactiveAncestor(glyph)).toBe(document.getElementById('icon-btn'));
    const climbed = buildTarget(glyph);
    expect(climbed.aria?.name).toBe('Delete');
    expect(climbed.fingerprint?.tagPath.endsWith('button')).toBe(true);
    const raw = buildTarget(glyph, { climb: false });
    expect(raw.fingerprint?.tagPath.endsWith('path')).toBe(true);
  });

  it('records a "n of m" position hint among same-tag siblings', () => {
    const links = document.querySelectorAll('li');
    const t = buildTarget(links[1]!, { climb: false });
    expect(t.hints?.position).toBe('2 of 2');
  });
});

describe('shadow DOM', () => {
  it('pierces open shadow roots and records the host path', () => {
    document.body.innerHTML = `<main><div id="widget-host"></div></main>`;
    const host = document.getElementById('widget-host')!;
    const shadow = host.attachShadow({ mode: 'open' });
    shadow.innerHTML = `<div class="panel"><button data-testid="shadow-save">Shadow save</button></div>`;
    const inner = shadow.querySelector('button')!;

    // a plain css query cannot see into the shadow root; pierce/ can
    expect(resolveSelector('css', '[data-testid="shadow-save"]', document)).toEqual([]);
    expect(resolveSelector('pierce', 'pierce/[data-testid="shadow-save"]', document)).toEqual([inner]);
    expect(resolveSelector('pierce', '[data-testid="shadow-save"]', document)).toEqual([inner]);

    expect(shadowPathOf(inner)).toEqual(['#widget-host']);
    expect(buildTarget(inner).shadowPath).toEqual(['#widget-host']);
  });

  it('resolves a shadow-DOM element captured from the light-DOM document', () => {
    document.body.innerHTML = `<main><div id="widget-host"></div></main>`;
    const shadow = document.getElementById('widget-host')!.attachShadow({ mode: 'open' });
    shadow.innerHTML = `<button data-testid="shadow-save">Shadow save</button>`;
    const inner = shadow.querySelector('button')!;
    const res = resolveTarget(document, buildTarget(inner));
    expect(res.element).toBe(inner);
    expect(res.healed).toBe(true); // the css testid selector cannot cross the boundary
  });
});

describe('frame path', () => {
  it('captures the iframe chain for an element inside a same-origin frame', () => {
    const iframe = document.createElement('iframe');
    iframe.id = 'app-frame';
    iframe.name = 'app';
    document.body.appendChild(iframe);
    const idoc = iframe.contentDocument!;
    idoc.body.innerHTML = `<button id="inner-save">Save</button>`;
    const inner = idoc.getElementById('inner-save')!;

    const t = buildTarget(inner);
    expect(t.frame).toHaveLength(1);
    expect(t.frame![0]!.selector).toBe('iframe#app-frame');
    expect(t.frame![0]!.name).toBe('app');
  });

  it('walks multiple hops top-down and never throws on a cross-origin boundary', () => {
    document.body.innerHTML = `<iframe name="outer"></iframe>`;
    const outer = document.querySelector('iframe')!;
    const innerEl = document.createElement('iframe');
    innerEl.id = 'inner';

    const top = { frameElement: null } as unknown as Window;
    (top as { parent: Window }).parent = top;
    const mid = { frameElement: outer, parent: top } as unknown as Window;
    const leaf = { frameElement: innerEl, parent: mid } as unknown as Window;

    expect(framePathOf(leaf).map((f) => f.selector)).toEqual(['iframe[name="outer"]', 'iframe#inner']);

    const crossOrigin = {
      get frameElement(): Element {
        throw new Error('blocked a frame with origin from accessing a cross-origin frame');
      },
    } as unknown as Window;
    expect(framePathOf(crossOrigin)).toEqual([]);
    expect(framePathOf(null)).toEqual([]);
    expect(buildTarget(document.getElementById('new-doc') ?? document.createElement('button')).frame).toEqual([]);
  });
});

describe('resolution cascade', () => {
  it('L0: resolves via primary selector on unchanged DOM', () => {
    const btn = document.getElementById('new-doc')!;
    const target = buildTarget(btn);
    const res = resolveTarget(document, target);
    expect(res.element).toBe(btn);
    expect(res.level).toBe(0);
    expect(res.via).toBe('primary');
    expect(res.healed).toBe(false);
    expect(res.tried.length).toBeGreaterThan(0);
  });

  it('L1: heals when testid and id are removed (falls back to aria/hash)', () => {
    const btn = document.getElementById('new-doc')!;
    const target = buildTarget(btn);
    btn.removeAttribute('data-testid');
    btn.removeAttribute('id');
    const res = resolveTarget(document, target);
    expect(res.element).toBe(btn);
    expect(res.level).toBeLessThanOrEqual(1);
    expect(res.healed).toBe(true);
  });

  it('heals when the id changes but the text and structure survive', () => {
    document.body.innerHTML = `<main><section aria-label="Settings"><h3>Settings</h3>
      <button id="save-profile" class="primary">Save profile</button></section></main>`;
    const btn = document.getElementById('save-profile')!;
    const target = buildTarget(btn);
    expect(target.selectors[0]!.value).toBe('#save-profile');

    btn.id = 'save-profile-v2'; // a rebuild renamed the id
    const res = resolveTarget(document, target);
    expect(res.element).toBe(btn);
    expect(res.healed).toBe(true);
    expect(res.via).toBe('fallback');
    expect(res.confidence).toBeGreaterThan(0.7);
  });

  it('L2: heals a reworded button via structural score', () => {
    const btn = document.getElementById('new-doc')!;
    const target = buildTarget(btn);
    btn.removeAttribute('data-testid');
    btn.removeAttribute('id');
    btn.textContent = 'Create new document'; // reworded
    const res = resolveTarget(document, target);
    expect(res.element).toBe(btn);
    expect(res.level).toBeLessThanOrEqual(2);
  });

  it('disambiguates same-named buttons by recorded container (no AI needed)', () => {
    document.body.innerHTML = `
      <main>
        <section aria-label="Billing">
          <h3>Billing</h3>
          <button class="act">Create</button>
        </section>
        <section aria-label="API keys">
          <h3>API keys</h3>
          <button class="act">Create</button>
        </section>
      </main>`;
    const billingBtn = document.querySelector('section[aria-label="Billing"] button')!;
    const apiBtn = document.querySelector('section[aria-label="API keys"] button')!;
    // record the API-keys "Create" (the second one — first-match would be wrong)
    const target = buildTarget(apiBtn);
    expect(target.hints?.container).toBe('API keys');
    const res = resolveTarget(document, target);
    expect(res.element).toBe(apiBtn); // not billingBtn
    // and the reverse: recording Billing resolves to Billing
    const res2 = resolveTarget(document, buildTarget(billingBtn));
    expect(res2.element).toBe(billingBtn);
  });

  it('still disambiguates after the primary selector is stripped (structural + container)', () => {
    document.body.innerHTML = `
      <main>
        <section aria-label="Billing"><h3>Billing</h3><button class="act">Create</button></section>
        <section aria-label="API keys"><h3>API keys</h3><button class="act">Create</button></section>
      </main>`;
    const apiBtn = document.querySelector('section[aria-label="API keys"] button')!;
    const target = buildTarget(apiBtn);
    // rename the accessible name so aria/text tiers can't exact-match — forces
    // structural scoring, which must still land in the right section
    document.querySelectorAll('button.act').forEach((b) => (b.textContent = 'Create key'));
    const res = resolveTarget(document, target);
    expect(res.element).toBe(apiBtn);
    expect(res.level).toBeLessThanOrEqual(2);
  });

  it('does not blind-fire when the page changed entirely', () => {
    const btn = document.getElementById('new-doc')!;
    const target = buildTarget(btn);
    document.body.innerHTML = '<main><button id="delete-all">Delete everything</button></main>';
    const res = resolveTarget(document, target);
    expect(res.element).toBeNull();
    expect(res.via).toBe('none');
    expect(res.healed).toBe(false);
    expect(res.confidence).toBe(0);
  });

  it('never resolves a selector hit to a wrong element (verification)', () => {
    const save = document.querySelector('form button')!;
    const target = buildTarget(save);
    // repurpose the button that occupies the same tag position
    save.textContent = 'Delete';
    save.setAttribute('aria-label', 'Delete');
    const res = resolveTarget(document, target);
    // either heals to nothing or to an element whose text/name agrees — must not return it as L0
    if (res.element) expect(res.level).toBeGreaterThanOrEqual(1);
  });

  it('grades a clean primary hit as high confidence', () => {
    const btn = document.getElementById('new-doc')!;
    const res = resolveTarget(document, buildTarget(btn));
    expect(res.element).toBe(btn);
    expect(res.confidence).toBeGreaterThanOrEqual(0.9);
  });

  it('never binds a button step onto a same-named link (role-family gate)', () => {
    const btn = document.getElementById('new-doc')!;
    const target = buildTarget(btn);
    expect(target.aria?.role).toBe('button');
    // the page now offers only a LINK with the same label — activating a link
    // navigates away, so a "click the button" step must NOT resolve onto it.
    document.body.innerHTML = '<main><a href="/new">New document</a></main>';
    const res = resolveTarget(document, target);
    expect(res.element).toBeNull();
  });

  it('grades a near-miss element (drifted id + reworded name) above an unrelated one (Similo)', () => {
    document.body.innerHTML = `<main>
      <button id="save-btn-v2" name="save" class="primary">Save changes</button>
      <button id="cancel" name="cancel" class="ghost">Discard</button>
    </main>`;
    const drifted = document.getElementById('save-btn-v2')!;
    const unrelated = document.getElementById('cancel')!;
    // what was recorded, before the id/name/text drifted a little
    const recorded = buildTarget(drifted);
    recorded.fingerprint!.attrs['id'] = 'save-btn';
    recorded.aria = { role: 'button', name: 'Save' };
    recorded.text = { content: 'Save', exact: false };

    const sDrift = scoreCandidate(drifted, recorded);
    const sOther = scoreCandidate(unrelated, recorded);
    // graded similarity keeps the drifted-but-same element well ahead of an
    // unrelated button — a binary equals would have zeroed the drifted id/name
    expect(sDrift).toBeGreaterThan(sOther);
    expect(sDrift).toBeGreaterThan(0.5);
  });

  it('demotes a purely positional match to low, heal-first confidence', () => {
    document.body.innerHTML = `<nav><a href="#">Item</a><a href="#">Item</a><a href="#">Item</a></nav>`;
    const second = document.querySelectorAll('nav a')[1]!;
    const target = buildTarget(second);
    // strip every identity-bearing selector, leaving only the nth-of-type path
    target.selectors = target.selectors.filter((s) => /:nth-/.test(s.value));
    expect(target.selectors.length).toBeGreaterThan(0);
    const res = resolveTarget(document, target);
    expect(res.element).toBe(second);
    expect(res.confidence).toBeLessThan(0.7); // below the actuation floor → runner heals first
  });

  it('re-matches a generated image after its alt text changes (new prompt)', () => {
    document.body.innerHTML = `
      <main>
        <article data-message-author-role="user"><img alt="uploaded avatar" /></article>
        <article data-message-author-role="assistant">
          <div class="markdown"><div class="img-wrap">
            <img alt="Generated image: Autumn forest with a contemplative fox" src="https://x/a.png" />
          </div></div>
        </article>
      </main>`;
    const img = document.querySelector('[data-message-author-role="assistant"] img')!;
    const target = buildTarget(img);

    // capture emits a generalizing prefix selector + a content-free structuralHash
    const values = target.selectors.map((s) => s.value);
    expect(values).toContain('img[alt^="Generated image"]');
    expect(target.fingerprint?.structuralHash).toBeTruthy();
    expect(target.fingerprint?.structuralHash).not.toBe(target.fingerprint?.elementHash);

    // replay: same page, DIFFERENT generated image content
    const live = document.querySelector('[data-message-author-role="assistant"] img')!;
    live.setAttribute('alt', 'Generated image: Blue butterfly on a yellow flower');
    live.setAttribute('src', 'https://x/b.png');

    const res = resolveTarget(document, target);
    expect(res.element).toBe(live);
    expect(res.healed).toBe(true);
  });

  it('resolves within a subtree root, not just a whole document', () => {
    const form = document.querySelector('form')!;
    const save = form.querySelector('button')!;
    const res = resolveTarget(form, buildTarget(save));
    expect(res.element).toBe(save);
  });
});

describe('targetFragility', () => {
  it('passes a target with a durable anchor', () => {
    expect(targetFragility(buildTarget(document.getElementById('new-doc')!)).level).toBe('ok');
  });

  it('flags a target anchored only to its position', () => {
    const weak: Target = {
      selectors: [
        { kind: 'css', value: 'div > div > span:nth-of-type(2)', score: 0.6, uniqueAtRecord: true },
        { kind: 'xpath', value: 'xpath//html/body/div/div/span[2]', score: 0.3 },
      ],
      fingerprint: { elementHash: 'a', stableHash: 'b', tagPath: 'html/body/div/div/span', attrs: {}, neighborText: [] },
    };
    const lint = targetFragility(weak);
    expect(lint.level).toBe('weak');
    expect(lint.reason).toContain('position');
  });
});

describe('preferLaidOut', () => {
  it('prefers a laid-out twin over a zero-size one sharing the accessible name', () => {
    // The real shape that broke a ChatGPT replay: the composer ships a hidden
    // <textarea> AND a visible contenteditable <div>, both named "Chat with
    // ChatGPT". isVisibleLenient passes both (neither is display:none), so
    // without this preference an aria fallback resolves to the textarea, reports
    // a confident hit, then fails actuation with "zero-size rect".
    const hidden = document.createElement('textarea');
    hidden.setAttribute('aria-label', 'Chat with ChatGPT');
    hidden.getBoundingClientRect = () => ({ width: 0, height: 0 }) as DOMRect;

    const visible = document.createElement('div');
    visible.setAttribute('role', 'textbox');
    visible.setAttribute('aria-label', 'Chat with ChatGPT');
    visible.getBoundingClientRect = () => ({ width: 667, height: 42 }) as DOMRect;

    expect(preferLaidOut([hidden, visible])).toEqual([visible]);
  });

  it('keeps every candidate when none reports layout (jsdom / detached doc)', () => {
    const a = document.createElement('button');
    const b = document.createElement('button');
    for (const el of [a, b]) el.getBoundingClientRect = () => ({ width: 0, height: 0 }) as DOMRect;
    expect(preferLaidOut([a, b])).toEqual([a, b]);
  });

  it('passes a single candidate through untouched', () => {
    const only = document.createElement('button');
    only.getBoundingClientRect = () => ({ width: 0, height: 0 }) as DOMRect;
    expect(preferLaidOut([only])).toEqual([only]);
  });
});
