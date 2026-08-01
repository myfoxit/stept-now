import { describe, expect, it } from 'vitest';

import { screenshotUrl, tourAppUrl } from './client';

describe('tourAppUrl', () => {
  it('links into the dashboard tour editor route', () => {
    expect(tourAppUrl('http://localhost:5273', 't1')).toBe('http://localhost:5273/tours/t1');
  });

  it('uses the dashboard origin, not the API origin', () => {
    // Regression: this used to be built from apiBase (:8600) with a /w/{ws}
    // prefix, so "Open in Stept" 404'd against FastAPI on every save.
    const url = tourAppUrl('https://app.example.com', 't1');
    expect(url).not.toContain(':8600');
    expect(url).not.toContain('/w/');
  });

  it('tolerates a trailing slash and escapes the id', () => {
    expect(tourAppUrl('https://app.example.com/', 'a b')).toBe(
      'https://app.example.com/tours/a%20b',
    );
  });
});

describe('screenshotUrl', () => {
  it('points at the public media route on the API origin', () => {
    expect(screenshotUrl('http://localhost:8600', 'w1', 'public/w1/shot.jpg')).toBe(
      'http://localhost:8600/api/widget/media/w1/public/w1/shot.jpg',
    );
  });
});
