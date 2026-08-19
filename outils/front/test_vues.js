/* La composition appartient au profil, les services à la maison.

   Une adresse, une clé, une sonde : la liste des services reste commune. Ce
   que chaque profil décide, c'est ce qui apparaît sur son écran, dans quel
   ordre, ce qui est en favori et quelles mesures il veut voir.
*/
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
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));
const dit = (bon, titre, detail) =>
  console.log((bon ? '  ok    ' : '  ECHEC ') + titre + (detail ? '   ' + detail : ''));

const app = (nom, cat) => ({ id: nom.toLowerCase(), nom, role: 'Service', cat,
  url: 'http://nas.local', logoUrl: '', token: '', apiKey: '', tempEntity: '',
  type: '', fav: false, masque: [] });

const noms = () => [...w.document.querySelectorAll('#apps-grid .app-card .app-name')]
  .map((n) => n.textContent);

setTimeout(async () => {
  inj(`
    apps = ${JSON.stringify(['Plex', 'Sonarr', 'Unraid', 'UniFi'].map((n) => app(n, 'Média')))};
    CONN = 'ok'; STATS = {}; i18n.langue = 'fr'; i18n.appliquer(document);
    USERS = [
      { nom: 'Guillaume', pwd: '1', photo: '',
        vue: { montre: null, ordre: ['plex','sonarr','unraid','unifi'],
               favoris: ['plex'], masques: {}, widgets: null } },
      { nom: 'Clara', pwd: '', photo: '',
        vue: { montre: ['unraid'], ordre: ['unraid'], favoris: [], masques: {}, widgets: null } },
    ];
    SESSION_USER = 'Guillaume'; ACTIVE = 'Guillaume';
    window.__pousses = 0;
    relayFetch = function (c) {
      if (c.indexOf('/cfg') === 0) { window.__pousses += 1; }
      return Promise.reject(new Error('hors ligne'));
    };
    renderApps();
  `);
  await attends(200);

  console.log('=== le profil qui suit la maison voit tout ===');
  dit(noms().join(' · ') === 'Plex · Sonarr · Unraid · UniFi', 'les quatre services',
      noms().join(' · '));
  dit([...w.document.querySelectorAll('#fav-grid .app-name')].map((n) => n.textContent)
      .join() === 'Plex', 'et son favori à lui');

  console.log('\n=== un autre profil, un autre tableau ===');
  inj("SESSION_USER = 'Clara'; ACTIVE = 'Clara'; renderApps();");
  await attends(150);
  dit(noms().join(' · ') === 'Unraid', 'Clara ne voit que ce qu\'elle a retenu', noms().join(' · '));
  dit($('btn-maison').hidden === false, 'et se voit proposer le reste de la maison',
      $('btn-maison').textContent);

  console.log('\n=== reprendre un service de la maison ===');
  $('btn-maison').click();
  await attends(100);
  const cases = [...$('maison-liste').querySelectorAll('input')];
  dit(cases.length === 3, 'les trois services absents sont proposés', String(cases.length));
  cases.forEach((c, i) => { c.checked = (i === 0); });
  $('maison-ok').click();
  await attends(150);
  dit(noms().join(' · ') === 'Unraid · Plex', 'celui qu\'elle coche entre dans son tableau',
      noms().join(' · '));

  console.log('\n=== un favori n\'appartient qu\'à celui qui le pose ===');
  inj("basculerFavori(apps.findIndex((a) => a.nom === 'Unraid'));");
  await attends(150);
  inj("window.__g = USERS[0].vue.favoris.join(); window.__c = USERS[1].vue.favoris.join();");
  dit(w.__c === 'unraid' && w.__g === 'plex',
      'celui de Clara ne touche pas celui de Guillaume',
      'Clara=' + w.__c + '  Guillaume=' + w.__g);
  dit(w.__pousses > 0, 'et la vue part au serveur');

  console.log('\n=== la liste des services, elle, ne bouge pas ===');
  inj("window.__apps = apps.map((a) => a.nom).join(' · ');");
  dit(w.__apps === 'Plex · Sonarr · Unraid · UniFi',
      'la maison déclare toujours les mêmes quatre', w.__apps);

  console.log('\n=== une mesure masquée ne l\'est que pour soi ===');
  /* Le masque range ses entrées sous « st:<mesure> » — même clé que la fiche. */
  inj(`USERS[1].vue.masques = { unraid: ['st:cpu'] };
       STATS = { unraid: [{ id: 'cpu', lab: 'CPU', val: '3 %', num: 3 },
                          { id: 'ram', lab: 'RAM', val: '20 %', num: 20 }] };
       voirStats = true; renderApps();
       window.__clara = document.querySelector('#apps-grid .app-card .app-mes').textContent;
       SESSION_USER = 'Guillaume'; ACTIVE = 'Guillaume'; renderApps();
       window.__guillaume = [...document.querySelectorAll('#apps-grid .app-card')]
         .filter((c) => c.querySelector('.app-name').textContent === 'Unraid')[0]
         .querySelector('.app-mes').textContent;`);
  await attends(150);
  dit(!w.__clara.includes('cpu') && w.__guillaume.includes('cpu'),
      'Clara masque son CPU, Guillaume le garde',
      'Clara « ' + w.__clara.trim() + ' » / Guillaume « ' + w.__guillaume.trim() + ' »');

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);
