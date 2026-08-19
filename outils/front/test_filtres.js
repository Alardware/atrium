/* Les compteurs d'état filtrent la grille, et se défont d'un second clic. */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const cible = process.argv[2] || path.join(__dirname, '..', '..', 'app', 'static', 'index.html');
const dom = new JSDOM(fs.readFileSync(cible, 'utf8'), {
  runScripts: 'dangerously', resources: 'usable', url: 'file:///' + cible, pretendToBeVisual: true });
const w = dom.window;
const ERREURS = []; w.addEventListener('error', (e) => ERREURS.push(String(e.message)));
w.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
w.fetch = () => Promise.reject(new Error('hors ligne'));
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));
const clic = (el) => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));

const app = (nom, cat) => ({ id: nom, nom, role: '', cat, url: 'http://x/' + nom,
  logoUrl: '', token: '', apiKey: '', tempEntity: '', type: '', fav: false, masque: [] });
const noms = () => [...w.document.querySelectorAll('#apps-grid .app-card .app-name')].map((n) => n.textContent);
const pastilles = () => [...w.document.querySelectorAll('#etat-filtres .ef')]
  .map((b) => b.textContent.replace(/\s+/g, ' ').trim() + (b.classList.contains('on') ? ' ✓' : ''));

setTimeout(async () => {
  inj('apps = ' + JSON.stringify(['Plex', 'Sonarr', 'Unraid', 'UniFi', 'Deluge']
        .map((n) => app(n, 'Média')))
    + '; CONN = "ok"; STATS = {}; i18n.langue = "fr";'
    + 'statusByName = { Plex: true, Sonarr: true, Unraid: true, UniFi: false, Deluge: null };'
    + 'SUPER = { releve: Date.now() / 1000, etats: {}, alertes: { total: 1, non_lues: 1,'
    + ' problemes: 1, niveau: "avertissement", alertes: [ { cle: "x", niveau: "avertissement",'
    + ' service: "Unraid", service_nom: "Unraid", code: "seuil", param: { metrique: "disque" },'
    + ' message: "x", depuis: Date.now() / 1000, lue: false } ] } };'
    + 'renderApps();');
  await attends(200);

  console.log('=== les compteurs ===');
  console.log('    ' + pastilles().join('   |   '));

  console.log('\n=== un clic sur « hors ligne » ===');
  clic(w.document.querySelector('[data-etat="off"]'));
  await attends(150);
  console.log('    grille : ' + noms().join(' · '));
  console.log('    compte en tête : ' + $('apps-count').textContent);
  console.log('    pastilles : ' + pastilles().join('   |   '));

  console.log('\n=== un clic sur « dégradé » ===');
  clic(w.document.querySelector('[data-etat="warn"]'));
  await attends(150);
  console.log('    grille : ' + noms().join(' · '));

  console.log('\n=== recliquer défait le filtre ===');
  clic(w.document.querySelector('[data-etat="warn"]'));
  await attends(150);
  console.log('    grille : ' + noms().join(' · '));

  console.log('\n=== moins de trois applications : pas de pastilles ===');
  inj('apps = apps.slice(0, 2); renderApps();');
  await attends(150);
  console.log('    rangée masquée : ' + $('etat-filtres').hidden);

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);
