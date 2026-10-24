// Browser test of the Launcher against docs/launcher-behaviour.md (#284): it clicks through every
// situation with Haal Centraal (launch/haalcentraal, real, ready in seconds) and the fake environments
// of scripts/launcher-preview.sh, which must be running.
//
//   cd tests/e2e && npm ci && node launcher-behaviour.js <preview state dir> [launcher URL]
//
// CHROME is the browser to drive (default: Chrome on Windows). Exit 1 when a rule fails.
//
// SPDX-License-Identifier: EUPL-1.2
const { chromium } = require("playwright-core");
const fs = require("fs");
const stateDir = process.argv[2];
const URL = process.argv[3] || "http://localhost:1900/";
const results = [];
const check = (ok, what) => { results.push(`${ok ? "OK  " : "FOUT"} ${what}`); console.log(results[results.length - 1]); };

(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROME || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true });
  const p = await b.newPage({ viewport: { width: 1500, height: 1100 } });
  const seenLabels = new Set(); const seenTexts = [];
  const cards = () => p.locator("section.card");
  const card = name => cards().filter({ has: p.locator(".title", { hasText: name }) });
  // What a card looks like: grey?, enabled controls, notes, unfoldable things.
  const look = async loc => loc.evaluate(el => ({
    grey: el.classList.contains("blocked"),
    enabled: [...el.querySelectorAll("button, a")].filter(x => !x.disabled && x.offsetParent !== null).map(x => x.innerText.trim()).filter(Boolean),
    notes: [...el.querySelectorAll(".blocked-note")].map(x => x.innerText.trim()),
    unfold: el.querySelectorAll("details").length,
    state: (el.querySelector(".state") || {}).innerText || "",
    text: el.innerText,
  }));
  const cycle = async () => p.locator("#cycle").evaluate(el => ({
    grey: !!el.querySelector(".cycle.blocked"),
    enabled: [...el.querySelectorAll("button")].filter(x => !x.disabled).map(x => x.innerText.trim()),
    text: el.innerText,
  }));
  const others = async (except) => {
    const out = [];
    for (let i = 0; i < await cards().count(); i++) {
      const l = await look(cards().nth(i));
      if (!l.text.includes(except)) out.push(l);
    }
    return out;
  };
  const inert = l => l.grey && l.enabled.length === 0 && l.notes.length === 0 && l.unfold === 0;
  const watch = async () => { // record progress labels and error texts
    const t = await p.locator("body").innerText();
    for (const l of await p.locator(".plabel").allInnerTexts()) if (l.trim()) seenLabels.add(l.trim());
    if (/TypeError|NetworkError|Dat lukte niet/.test(t)) seenTexts.push(t.match(/.*(TypeError|NetworkError|Dat lukte niet).*/)[0]);
  };
  const waitFor = async (cond, secs) => { for (let i = 0; i < secs * 2; i++) { await watch(); if (await cond()) return true; await p.waitForTimeout(500); } return false; };
  try { fs.unlinkSync(`${stateDir}/haalcentraal-fail-at`); } catch {}

  await p.goto(URL); await p.waitForTimeout(3000);
  // 1. Idle
  let all = await others("§none§");
  check(all.every(l => !l.grey && l.enabled.includes("Starten")), "niets actief: elke kaart normaal, met Starten");
  // The page's first answer can take a while (the checks ask Docker).
  await waitFor(async () => (await cycle()).enabled.includes("Rondgang nu starten"), 30);
  let c = await cycle();
  check(!c.grey && c.enabled.includes("Rondgang nu starten"), "niets actief: rondgang normaal, Rondgang nu starten");

  // 2. Start Haal Centraal: at once the situation 'X active'
  await card("Haal Centraal").locator("button", { hasText: "Starten" }).click();
  await p.waitForTimeout(300);
  let hc = await look(card("Haal Centraal"));
  let rest = await others("Haal Centraal");
  check(/gestart/i.test(hc.state), `direct na de klik: Haal Centraal 'Wordt gestart' (was: '${hc.state.trim()}')`);
  check(rest.every(inert), `direct na de klik: de andere kaarten grijs en inert (${rest.filter(l => !inert(l)).map(l => JSON.stringify({grey: l.grey, enabled: l.enabled, notes: l.notes.length, unfold: l.unfold})).slice(0, 2).join(" ")})`);
  c = await cycle(); check(c.grey && c.enabled.length === 0, `direct na de klik: rondgang grijs en inert (${JSON.stringify(c.enabled)})`);

  // 3. Ready
  const ready = await waitFor(async () => /Klaar/.test((await look(card("Haal Centraal"))).state), 60);
  hc = await look(card("Haal Centraal"));
  check(ready && hc.enabled.join(",") === "Openen,Stoppen", `klaar: Openen en Stoppen (${JSON.stringify(hc.enabled)})`);
  rest = await others("Haal Centraal");
  check(rest.every(inert), "klaar: de andere kaarten nog steeds grijs en inert");
  check(![...seenLabels].some(l => /stilzetten/i.test(l)), `geen stap 'andere omgevingen stilzetten' (stappen: ${[...seenLabels].join(" | ")})`);
  check(seenTexts.length === 0, `geen technische foutmelding (${seenTexts.slice(0, 2).join(" | ")})`);
  const [page2] = await Promise.all([p.context().waitForEvent("page"), card("Haal Centraal").locator("a", { hasText: "Openen" }).click()]);
  await page2.waitForLoadState(); await page2.locator("button", { hasText: "Opvragen" }).click(); await page2.waitForTimeout(2000);
  check((await page2.locator("body").innerText()).includes("Suzanne Moulin"), "Openen: de BRP-pagina vraagt testpersoon Suzanne Moulin op");
  await page2.close();

  // 4. Stop: back to idle
  await card("Haal Centraal").locator("button", { hasText: "Stoppen" }).click();
  const idle = await waitFor(async () => (await others("§none§")).every(l => !l.grey && l.enabled.includes("Starten")), 30);
  check(idle, "na Stoppen: weer niets actief, elke kaart Starten");

  // 5. Failure: step 2 fails on purpose
  fs.writeFileSync(`${stateDir}/haalcentraal-fail-at`, "2");
  await card("Haal Centraal").locator("button", { hasText: "Starten" }).click();
  const failed = await waitFor(async () => /mislukt/i.test((await look(card("Haal Centraal"))).state), 30);
  hc = await look(card("Haal Centraal"));
  check(failed && hc.enabled.join(",") === "Opnieuw proberen,Stoppen", `mislukt: 'Starten mislukt', Opnieuw proberen en Stoppen (${hc.state.trim()} ${JSON.stringify(hc.enabled)})`);
  check(/mislukt/.test(hc.text) && !/TypeError|NetworkError/.test(hc.text), "mislukt: de fout in woorden");
  check((await others("Haal Centraal")).every(inert), "mislukt: de andere kaarten grijs en inert");
  fs.unlinkSync(`${stateDir}/haalcentraal-fail-at`);
  await card("Haal Centraal").locator("button", { hasText: "Opnieuw proberen" }).click();
  const ready2 = await waitFor(async () => /Klaar/.test((await look(card("Haal Centraal"))).state), 60);
  check(ready2, "Opnieuw proberen: daarna Klaar");
  await card("Haal Centraal").locator("button", { hasText: "Stoppen" }).click();
  await waitFor(async () => (await others("§none§")).every(l => !l.grey), 30);

  // 6. The round
  await p.locator("#cycle button", { hasText: "Rondgang nu starten" }).click();
  await p.waitForTimeout(500);
  c = await cycle();
  check(!c.grey && c.enabled.includes("Rondgang stoppen") && !c.enabled.includes("Rondgang nu starten"), `rondgang: Rondgang stoppen (${JSON.stringify(c.enabled)})`);
  await waitFor(async () => false, 4);
  all = await others("§none§");
  const active = all.filter(l => !l.grey);
  check(active.length <= 1 && active.every(l => l.enabled.every(x => x === "Openen")), `rondgang: hoogstens één kaart actief, zonder Starten/Stoppen (${JSON.stringify(active.map(l => l.enabled))})`);
  check(all.filter(l => l.grey).every(inert), "rondgang: de andere kaarten grijs en inert");
  await p.locator("#cycle button", { hasText: "Rondgang stoppen" }).click();
  const idle2 = await waitFor(async () => (await cycle()).enabled.includes("Rondgang nu starten"), 30);
  check(idle2, "Rondgang stoppen: weer Rondgang nu starten");
  const idle3 = await waitFor(async () => (await others("§none§")).every(l => !l.grey && l.enabled.includes("Starten")), 30);
  check(idle3, "Rondgang stoppen: weer niets actief, elke kaart Starten (ook de omgeving van de rondgang is gestopt)");

  console.log(`\n${results.filter(r => r.startsWith("OK")).length} OK, ${results.filter(r => r.startsWith("FOUT")).length} FOUT`);
  await b.close();
  process.exit(results.some(r => r.startsWith("FOUT")) ? 1 : 0);
})().catch(e => { console.error("ERROR", e.message); process.exit(1); });
