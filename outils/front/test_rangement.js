/* Ranger les tuiles à la main : seulement en mode création, et l'ordre tient. */
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
const ENVOIS = [];
w.fetch = (u, o) => {
  ENVOIS.push(String(u) + ' ' + ((o && o.body) || '').slice(0, 120));
  return Promise.reject(new Error('hors ligne'));
};
const inj = (c) => { const s = w.document.createElement('script'); s.textContent = c; w.document.body.appendChild(s); s.remove(); };
const $ = (id) => w.document.getElementById(id);
const attends = (ms) => new Promise((r) => setTimeout(r, ms));

const app = (nom, cat) => ({ id: nom, nom, role: '', cat, url: 'http://x/' + nom,
  logoUrl: '', token: '', apiKey: '', tempEntity: '', type: '', fav: false, masque: [] });

/* Le transfert de données que le navigateur promène pendant un glisser. */
function paquet() {
  const magasin = {};
  return { effectAllowed: '', setData: (t, v) => { magasin[t] = v; },
           getData: (t) => magasin[t] };
}

function glisser(depuis, vers) {
  const dt = paquet();
  const ev = (type, cible) => {
    const e = new w.Event(type, { bubbles: true, cancelable: true });
    e.dataTransfer = dt;
    cible.dispatchEvent(e);
    return e;
  };
  ev('dragstart', depuis);
  ev('dragover', vers);
  const drop = ev('drop', vers);
  ev('dragend', depuis);
  return drop;
}

const noms = () => [...w.document.querySelectorAll('#apps-grid .app-card .app-name')]
  .map((n) => n.textContent);

setTimeout(async () => {
  inj('apps = ' + JSON.stringify([app('Plex', 'Média'), app('Sonarr', 'Média'),
                                  app('Unraid', 'Serveur'), app('UniFi', 'Réseau')])
    + '; CONN = "ok"; STATS = {}; i18n.langue = "fr"; creatif = false;'
    + 'document.documentElement.dataset.creatif = "0"; renderApps();');
  await attends(200);

  console.log('=== hors mode création, rien ne bouge ===');
  let cartes = [...w.document.querySelectorAll('#apps-grid .app-card')];
  console.log('    tuiles saisissables : ' + cartes.filter((c) => c.draggable).length);
  const empeche = glisser(cartes[0], cartes[2]).defaultPrevented;
  console.log('    ordre après un glisser : ' + noms().join(' · '));

  console.log('\n=== en mode création, la tuile se range ===');
  inj('creatif = true; document.documentElement.dataset.creatif = "1"; renderApps();');
  await attends(150);
  cartes = [...w.document.querySelectorAll('#apps-grid .app-card')];
  console.log('    tuiles saisissables : ' + cartes.filter((c) => c.draggable).length);
  console.log('    avant : ' + noms().join(' · '));
  glisser(cartes[0], cartes[2]);
  await attends(150);
  console.log('    après : ' + noms().join(' · '));
  inj('window.__o = apps.map((a) => a.nom).join(" · ");');
  console.log('    dans la configuration : ' + w.__o);
  console.log('    poussé au serveur : '
    + (ENVOIS.some((e) => e.includes('/cfg')) ? 'oui' : '!!! non'));

  console.log('\n=== une tuile lâchée sur elle-même ne change rien ===');
  cartes = [...w.document.querySelectorAll('#apps-grid .app-card')];
  glisser(cartes[1], cartes[1]);
  await attends(100);
  console.log('    ordre : ' + noms().join(' · '));

  console.log('\n=== filtrée par catégorie, la place reste la vraie ===');
  inj('activeCat = "Média"; renderApps();');
  await attends(150);
  console.log('    vue : ' + noms().join(' · '));
  cartes = [...w.document.querySelectorAll('#apps-grid .app-card')];
  glisser(cartes[1], cartes[0]);
  await attends(150);
  inj('window.__o = apps.map((a) => a.nom).join(" · ");');
  console.log('    configuration : ' + w.__o);

  console.log('\nerreurs page :', ERREURS.length ? ERREURS : 'aucune');
  process.exit(0);
}, 2000);
