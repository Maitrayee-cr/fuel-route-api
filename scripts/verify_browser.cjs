// Optional browser smoke test. Requires Playwright and a running local app.
// BASE_URL=http://127.0.0.1:8000 node scripts/verify_browser.cjs
// BROWSER_EXECUTABLE may point to an existing Chrome installation.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {}),
  });
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = process.env.BASE_URL || 'http://127.0.0.1:8000';
    await page.goto(base);
    await page.locator('#initial-price').fill('3.5');
    const responsePromise = page.waitForResponse(response => response.url().endsWith('/api/routes/'));
    await page.locator('#submit').click();
    const response = await responsePromise;
    assert.equal(response.status(), 201, await response.text());
    await page.waitForURL('**/maps/**/', {timeout: 60000});
    await page.waitForSelector('.leaflet-overlay-pane svg path');
    await page.waitForFunction(() => [...document.querySelectorAll('.leaflet-tile')]
      .some(tile => tile.complete && tile.naturalWidth > 0), null, {timeout: 20000});
    const result = {
      routePaths: await page.locator('.leaflet-overlay-pane svg path').count(),
      markers: await page.locator('.badge').count(),
      consumptionCostVisible: await page.getByText('Value of all fuel consumed:').count() === 1,
    };
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForFunction(() => {
      const map = document.getElementById('map').getBoundingClientRect();
      return [...document.querySelectorAll('.badge')].every(marker => {
        const box = marker.getBoundingClientRect();
        return box.left >= map.left && box.right <= map.right && box.top >= map.top && box.bottom <= map.bottom;
      });
    }, null, {timeout: 5000});
    result.mobileMarkersVisible = true;
    result.mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    await page.goto(base);
    await page.locator('#start').fill('{"lat":43.65,"lon":-79.38}');
    await page.locator('#submit').click();
    await page.waitForFunction(() => document.getElementById('status').textContent.includes('USA'));
    result.invalidCountryMessage = await page.locator('#status').textContent();
    result.javascriptErrors = errors;
    assert.equal(errors.length, 0);
    assert.equal(result.mobileOverflow, false);
    assert.ok(result.markers >= 3 && result.consumptionCostVisible);
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
