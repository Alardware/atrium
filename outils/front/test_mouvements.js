/* Les mouvements ne se jouent qu'au changement : une grille redessinée à
   l'identique doit rester immobile. */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');
const cible = process.argv[2] || path.join(__dirname, '..', '..', 'app', 'static', 'index.html');
const dom = new JSDOM(fs.readFileSync(cible, 'utf8'), {
  runScripts: 'dangerously', resources: 'usable',
  url: 'file:///' + cible, pretendToBeVisual: true });
const w = dom.window;
const ERREURS = []; w.addEventListener('error', (e) => ERREURS.push(String(e.message)));
w.WebSocket = function () { this.close = () => {}; this.send = () => {}; };
w.fetch = () => Promise.reject(new Error('hors ligne'));
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const attends = (ms) => new Promise((r) => setTimeout(r, ms));

const app = (nom, cat) => ({ id: nom.toLowerCase(), nom, role: 'Service', cat,
  url: 'http://192.168.0.9', logoUrl: '', token: '', apiKey: '', tempEntity: '',
  type: '', fav: false, masque: [] });

const compte = (sel) => w.document.querySelectorAll(sel).length;
const dit = (bon, titre, detail) =>
  console.log((bon ? '  ok    ' : '  ECHEC ') + titre + (detail ? '   ' + detail : ''));

setTimeout(async () => {
  inj(`
    apps = ${JSON.stringify(['Plex', 'Sonarr', 'Radarr', 'Unraid'].map((n) => app(n, 'Média')))};
    CONN = 'ok'; i18n.langue = 'fr'; voirStats = true;
    STATS = { plex: [{ id: 'lectures', lab: 'lectures', val: '2' }],
              sonarr: [{ id: 'manque', lab: 'manque', val: '14' }],
              radarr: [{ id: 'manque', lab: 'manque', val: '3' }],
              unraid: [{ id: 'cpu', lab: 'cpu', val: '3 %' },
                       { id: 'ram', lab: 'ram', val: '20 %' }] };
    SUPER = { etats: {}, releve: 0,
              alertes: { total: 0, non_lues: 0, problemes: 0, niveau: '', alertes: [] } };
    statusByName = { plex: { up: true }, sonarr: { up: true },
                     radarr: { up: true }, unraid: { up: true } };
    sante = function (c) { return window.__sante[c] || 'on'; };
    window.__sante = { plex: 'on', sonarr: 'on', radarr: 'on', unraid: 'on' };
    renderApps();
  `);
  await attends(200);

  console.log('=== premier affichage : la grille se pose en cascade ===');
  dit(compte('#apps-grid .app-card.t-arrivee') === 4, 'les quatre tuiles arrivent',
      compte('#apps-grid .app-card.t-arrivee') + ' / 4');
  const retards = [...w.document.querySelectorAll('#apps-grid .app-card')]
    .map((c) => c.style.getPropertyValue('--i'));
  dit(retards.join(',') === '0,1,2,3', 'chacune avec son retard', retards.join(','));

  console.log('\n=== relevé suivant, rien n\'a changé : la grille ne bouge pas ===');
  inj('renderApps();');
  await attends(150);
  dit(compte('#apps-grid .app-card.t-arrivee') === 0, 'aucune tuile ne rejoue son arrivée',
      compte('#apps-grid .app-card.t-arrivee') + ' animée(s)');
  dit(compte('#apps-grid .maj') === 0, 'aucun chiffre ne clignote');

  console.log('\n=== une mesure change : elle seule s\'éclaire ===');
  inj(`STATS.unraid = [{ id: 'cpu', lab: 'cpu', val: '47 %' },
                       { id: 'ram', lab: 'ram', val: '20 %' }]; renderApps();`);
  await attends(150);
  const eclaires = [...w.document.querySelectorAll('#apps-grid .maj')]
    .map((e) => e.textContent.trim() || e.parentElement.textContent.trim());
  dit(eclaires.length === 1, 'une seule mesure signalée', JSON.stringify(eclaires));
  dit(compte('#apps-grid .app-card.t-arrivee') === 0, 'et la grille reste immobile');

  console.log('\n=== le même relevé de nouveau : le chiffre ne rejoue pas ===');
  inj('renderApps();');
  await attends(150);
  dit(compte('#apps-grid .maj') === 0, 'plus rien ne clignote',
      compte('#apps-grid .maj') + ' element(s)');

  console.log('\n=== un service tombe : sa pastille fait une onde ===');
  inj(`window.__sante.sonarr = 'off'; renderApps();`);
  await attends(150);
  const onde = [...w.document.querySelectorAll('#apps-grid .app-card')]
    .filter((c) => c.querySelector('.bascule'))
    .map((c) => c.querySelector('.app-name').textContent);
  dit(onde.length === 1 && onde[0] === 'Sonarr', 'seule la tuile qui bascule',
      JSON.stringify(onde));
  inj('renderApps();');
  await attends(150);
  dit(compte('#apps-grid .bascule') === 0, 'et pas au relevé suivant');

  console.log('\n=== on filtre : les tuiles retenues se reposent ===');
  inj(`filtreEtat = 'on'; renderApps();`);
  await attends(150);
  dit(compte('#apps-grid .app-card.t-arrivee') === 3, 'les trois restantes arrivent',
      compte('#apps-grid .app-card.t-arrivee') + ' / 3');

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);
